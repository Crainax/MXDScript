from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from logging import Logger
from pathlib import Path

from mhscript_yjs.core.config import YjsSettings


class YjsError(RuntimeError):
    pass


@dataclass
class YjsDevice:
    settings: YjsSettings
    logger: Logger | None = None

    def __post_init__(self) -> None:
        self._dll: ctypes.WinDLL | None = None
        self._handle: int | None = None

    def __enter__(self) -> YjsDevice:
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def handle(self) -> int:
        if not self._handle:
            raise YjsError("YiJianShu device is not open")
        return self._handle

    def open(self) -> None:
        dll_path = self.settings.dll_path
        if not dll_path.exists():
            raise YjsError(f"msdk.dll not found: {dll_path}")

        self._dll = ctypes.WinDLL(str(dll_path))
        self._declare_api(self._dll)
        self._handle = self._open_handle(self._dll)
        if self._is_invalid_handle(self._handle):
            raise YjsError(
                "Failed to open YiJianShu device "
                f"(mode={self.settings.open_mode}, port={self.settings.port}, "
                f"vid=0x{self.settings.vid:04X}, pid=0x{self.settings.pid:04X})"
            )

        if self.settings.absolute_move:
            self._check(
                "M_ResolutionUsed",
                self._dll.M_ResolutionUsed(
                    self.handle,
                    self.settings.screen_width,
                    self.settings.screen_height,
                ),
            )

        if self.logger:
            self.logger.info(
                "yjs_opened handle=%s mode=%s screen=%sx%s",
                self._handle,
                self.settings.open_mode,
                self.settings.screen_width,
                self.settings.screen_height,
            )
            self._log_device_identity()

    def close(self) -> None:
        if not self._dll or not self._handle:
            return
        try:
            self.release_all_keys()
            result = self._dll.M_Close(self._handle)
            if self.logger:
                self.logger.info("yjs_closed result=%s", result)
        finally:
            self._handle = None
            self._dll = None

    def release_all_keys(self) -> None:
        if self._dll and self._handle:
            result = self._dll.M_ReleaseAllKey(self._handle)
            if self.logger:
                self.logger.debug("release_all_keys result=%s", result)

    def press_key(self, key_code: int, count: int = 1) -> None:
        result = self._dll_checked().M_KeyPress2(self.handle, key_code, count)
        self._check("M_KeyPress2", result)
        if self.logger:
            self.logger.info("key_press key_code=%s count=%s", key_code, count)

    def move_to(self, x: int, y: int, *, smooth: bool = True) -> None:
        api_name = self._choose_move_api(x, y, smooth=smooth)
        result = getattr(self._dll_checked(), api_name)(self.handle, x, y)
        self._check(api_name, result, x=x, y=y)
        if self.logger:
            self.logger.info("mouse_move api=%s x=%s y=%s smooth=%s", api_name, x, y, smooth)

    def left_click(self, count: int = 1) -> None:
        result = self._dll_checked().M_LeftClick(self.handle, count)
        self._check("M_LeftClick", result)
        if self.logger:
            self.logger.info("left_click count=%s", count)

    def _dll_checked(self) -> ctypes.WinDLL:
        if not self._dll:
            raise YjsError("YiJianShu DLL is not loaded")
        return self._dll

    def _open_handle(self, dll: ctypes.WinDLL) -> int:
        mode = self.settings.open_mode.lower()
        if mode == "port":
            return int(dll.M_Open(self.settings.port) or 0)
        if mode == "scan":
            return int(dll.M_ScanAndOpen() or 0)
        if mode == "vidpid":
            return int(dll.M_Open_VidPid(self.settings.vid, self.settings.pid) or 0)
        raise YjsError(f"Unsupported yjs.open_mode: {self.settings.open_mode}")

    @staticmethod
    def _is_invalid_handle(handle: int | None) -> bool:
        return handle in {None, 0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF}

    def _log_device_identity(self) -> None:
        if not self._dll or not self.logger:
            return
        try:
            ids = {
                "master_vid": self._dll.M_GetVidPid(self.handle, 1),
                "master_pid": self._dll.M_GetVidPid(self.handle, 2),
                "slave_vid": self._dll.M_GetVidPid(self.handle, 3),
                "slave_pid": self._dll.M_GetVidPid(self.handle, 4),
            }
            self.logger.info("yjs_vid_pid=%s", ids)
        except Exception as exc:  # pragma: no cover - diagnostics only
            self.logger.warning("failed_to_read_vid_pid error=%r", exc)

    def _choose_move_api(self, x: int, y: int, *, smooth: bool) -> str:
        configured = self.settings.move_api.lower()
        if configured == "move_to2":
            return "M_MoveTo2"
        if configured == "move_to3":
            return "M_MoveTo3" if smooth else "M_MoveTo3_D"
        if configured != "auto":
            raise YjsError(f"Unsupported yjs.move_api: {self.settings.move_api}")

        outside_configured_screen = (
            x < 0
            or y < 0
            or x >= self.settings.screen_width
            or y >= self.settings.screen_height
        )
        if outside_configured_screen:
            if self.logger:
                self.logger.info(
                    "move_api_auto_fallback api=M_MoveTo2 x=%s y=%s configured_screen=%sx%s",
                    x,
                    y,
                    self.settings.screen_width,
                    self.settings.screen_height,
                )
            return "M_MoveTo2"
        return "M_MoveTo3" if smooth else "M_MoveTo3_D"

    def _check(self, api_name: str, result: int, *, x: int | None = None, y: int | None = None) -> None:
        if result != 0:
            detail = f"{api_name} failed with result={result}"
            if x is not None and y is not None:
                detail += (
                    f" at x={x}, y={y}, configured_screen="
                    f"{self.settings.screen_width}x{self.settings.screen_height}"
                )
            raise YjsError(detail)

    @staticmethod
    def _declare_api(dll: ctypes.WinDLL) -> None:
        handle = wintypes.HANDLE
        int_arg = ctypes.c_int

        dll.M_Open.restype = handle
        dll.M_Open.argtypes = [int_arg]
        dll.M_Open_VidPid.restype = handle
        dll.M_Open_VidPid.argtypes = [int_arg, int_arg]
        dll.M_ScanAndOpen.restype = handle
        dll.M_ScanAndOpen.argtypes = []
        dll.M_Close.restype = int_arg
        dll.M_Close.argtypes = [handle]

        dll.M_GetVidPid.restype = int_arg
        dll.M_GetVidPid.argtypes = [handle, int_arg]
        dll.M_GetDevSn.restype = int_arg
        dll.M_GetDevSn.argtypes = [
            handle,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(ctypes.c_ubyte),
        ]

        dll.M_ReleaseAllKey.restype = int_arg
        dll.M_ReleaseAllKey.argtypes = [handle]
        dll.M_KeyPress2.restype = int_arg
        dll.M_KeyPress2.argtypes = [handle, int_arg, int_arg]

        dll.M_LeftClick.restype = int_arg
        dll.M_LeftClick.argtypes = [handle, int_arg]
        dll.M_ResolutionUsed.restype = int_arg
        dll.M_ResolutionUsed.argtypes = [handle, int_arg, int_arg]
        dll.M_MoveTo2.restype = int_arg
        dll.M_MoveTo2.argtypes = [handle, int_arg, int_arg]
        dll.M_MoveTo3.restype = int_arg
        dll.M_MoveTo3.argtypes = [handle, int_arg, int_arg]
        dll.M_MoveTo3_D.restype = int_arg
        dll.M_MoveTo3_D.argtypes = [handle, int_arg, int_arg]


def validate_dll_architecture(dll_path: Path) -> None:
    if not dll_path.exists():
        raise YjsError(f"msdk.dll not found: {dll_path}")
