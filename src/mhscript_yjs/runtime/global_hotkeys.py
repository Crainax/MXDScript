from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass


WM_HOTKEY = 0x0312
WM_APP_COMMAND = 0x8000 + 101
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000


@dataclass(frozen=True)
class HotkeyBinding:
    name: str
    shortcut: str
    modifiers: int
    vk: int
    callback: Callable[[], None]


class GlobalHotkeyService:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._commands: queue.Queue[tuple[str, Sequence[HotkeyBinding]]] = queue.Queue()
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    def replace_bindings(self, bindings: Sequence[HotkeyBinding]) -> None:
        if sys.platform != "win32":
            return
        self._ensure_started()
        self._commands.put(("replace", tuple(bindings)))
        self._post_command()

    def stop(self) -> None:
        if sys.platform != "win32":
            return
        with self._lock:
            if self._thread is None:
                return
            self._commands.put(("stop", ()))
            self._post_command()

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="mxdscript-global-hotkeys",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=2):
            raise RuntimeError("Windows 全局热键线程启动超时。")

    def _post_command(self) -> None:
        if not self._thread_id:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW(self._thread_id, WM_APP_COMMAND, 0, 0)

    def _thread_main(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_win32(user32, kernel32)

        self._thread_id = int(kernel32.GetCurrentThreadId())
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)
        self._ready.set()

        callbacks: dict[int, Callable[[], None]] = {}
        registered_ids: set[int] = set()

        def unregister_all() -> None:
            for hotkey_id in list(registered_ids):
                user32.UnregisterHotKey(None, hotkey_id)
            registered_ids.clear()
            callbacks.clear()

        def register_all(bindings: Sequence[HotkeyBinding]) -> None:
            unregister_all()
            for index, binding in enumerate(bindings, start=1):
                hotkey_id = 5100 + index
                ok = user32.RegisterHotKey(None, hotkey_id, binding.modifiers, binding.vk)
                if not ok:
                    error = ctypes.get_last_error()
                    self.logger.warning(
                        "全局热键注册失败：%s (%s)，Windows 错误=%s。",
                        binding.name,
                        binding.shortcut,
                        error,
                    )
                    continue
                registered_ids.add(hotkey_id)
                callbacks[hotkey_id] = binding.callback
                self.logger.debug(
                    "global_hotkey_registered name=%s shortcut=%s id=%s",
                    binding.name,
                    binding.shortcut,
                    hotkey_id,
                )

        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    self.logger.warning("Windows 全局热键消息循环读取失败。")
                    break
                if msg.message == WM_HOTKEY:
                    callback = callbacks.get(int(msg.wParam))
                    if callback:
                        try:
                            callback()
                        except Exception:
                            self.logger.exception("全局热键回调执行失败。")
                    continue
                if msg.message == WM_APP_COMMAND:
                    should_stop = False
                    try:
                        while True:
                            command, bindings = self._commands.get_nowait()
                            if command == "replace":
                                register_all(bindings)
                            elif command == "stop":
                                should_stop = True
                    except queue.Empty:
                        pass
                    if should_stop:
                        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        finally:
            unregister_all()
            self._thread_id = 0


def _configure_win32(user32: ctypes.WinDLL, kernel32: ctypes.WinDLL) -> None:
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
