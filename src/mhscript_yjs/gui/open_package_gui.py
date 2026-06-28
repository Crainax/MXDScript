from __future__ import annotations

import argparse
import ctypes
import logging
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from mhscript_yjs.core.config import load_config
from mhscript_yjs.runtime.control import PauseController
from mhscript_yjs.runtime.logging import setup_script_logger
from mhscript_yjs.runtime.mouse_settings import MousePointerPrecisionManager
from mhscript_yjs.scripts.tool.open_package import create_runner


LRESULT = wintypes.LPARAM
HICON = wintypes.HANDLE
HCURSOR = wintypes.HANDLE
HBRUSH = wintypes.HANDLE
HMODULE = wintypes.HANDLE
HMENU = wintypes.HANDLE
LPVOID = ctypes.c_void_p
UINT_PTR = ctypes.c_size_t
ATOM = ctypes.c_ushort
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


ERROR_CLASS_ALREADY_EXISTS = 1410
IDC_ARROW = 32512

COLOR_WINDOW = 5
CW_USEDEFAULT = -2147483648
SW_SHOW = 5

WS_OVERLAPPED = 0x00000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000

BS_PUSHBUTTON = 0x00000000
SS_CENTER = 0x00000001

BN_CLICKED = 0
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_HOTKEY = 0x0312

VK_F10 = 0x79
HOTKEY_ID_TOGGLE = 3001

MB_ICONERROR = 0x00000010
MB_ICONWARNING = 0x00000030


@dataclass(frozen=True)
class GuiOptions:
    config: Path | None
    dry_run: bool
    skip_delays: bool


class QueueStatusHandler(logging.Handler):
    def __init__(self, events: queue.Queue[tuple[str, str]]) -> None:
        super().__init__(level=logging.INFO)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        self.events.put(("log", self.format(record)))


class NativeOpenPackageGui:
    BUTTON_ID = 1001
    TIMER_ID = 2001

    def __init__(self, options: GuiOptions) -> None:
        self.options = options
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.controller: PauseController | None = None
        self.worker: threading.Thread | None = None
        self.mouse_precision: MousePointerPrecisionManager | None = None
        self.state = "idle"
        self.close_requested = False
        self.hotkey_registered = False

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_win32_api()
        self.hinstance = self.kernel32.GetModuleHandleW(None)
        self.class_name = "MXDScriptOpenPackageWindow"
        self._wnd_proc = WNDPROC(self._handle_message)
        self._register_window_class()

        self.hwnd = self._create_main_window()
        self.button_hwnd = self._create_child(
            class_name="BUTTON",
            text="开始",
            style=WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
            x=90,
            y=28,
            width=180,
            height=64,
            control_id=self.BUTTON_ID,
        )
        self.mode_hwnd = self._create_child(
            class_name="STATIC",
            text="模式: dry-run" if options.dry_run else "模式: 易键鼠 live",
            style=WS_CHILD | WS_VISIBLE | SS_CENTER,
            x=24,
            y=106,
            width=312,
            height=24,
            control_id=0,
        )
        self.status_hwnd = self._create_child(
            class_name="STATIC",
            text="准备就绪",
            style=WS_CHILD | WS_VISIBLE | SS_CENTER,
            x=24,
            y=134,
            width=312,
            height=24,
            control_id=0,
        )
        self._register_hotkey()

    def run(self) -> int:
        self.user32.ShowWindow(self.hwnd, SW_SHOW)
        self.user32.UpdateWindow(self.hwnd)
        self.user32.SetTimer(self.hwnd, self.TIMER_ID, 100, None)

        msg = wintypes.MSG()
        while self.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self.user32.TranslateMessage(ctypes.byref(msg))
            self.user32.DispatchMessageW(ctypes.byref(msg))
        return int(msg.wParam)

    def toggle(self) -> None:
        if self.state in {"idle", "finished", "error"}:
            self.start()
        elif self.state == "running":
            self.pause()
        elif self.state == "paused":
            self.resume()

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self._prepare_mouse_precision():
            return
        self.controller = PauseController()
        self.state = "running"
        self._set_button_text("暂停")
        self._set_status("正在启动...")
        self.worker = threading.Thread(target=self._run_script, daemon=True)
        self.worker.start()

    def pause(self) -> None:
        if not self.controller:
            return
        self.controller.pause()
        self._restore_mouse_precision()
        self.state = "paused"
        self._set_button_text("继续")
        self._set_status("已暂停")

    def resume(self) -> None:
        if not self.controller:
            return
        if not self._prepare_mouse_precision():
            return
        self.controller.resume()
        self.state = "running"
        self._set_button_text("暂停")
        self._set_status("运行中")

    def close(self) -> None:
        if self.controller and self.worker and self.worker.is_alive():
            self.close_requested = True
            self.controller.stop()
            self._restore_mouse_precision()
            self._set_status("正在停止...")
            self.user32.EnableWindow(self.button_hwnd, False)
            return
        self.user32.DestroyWindow(self.hwnd)

    def poll_events(self) -> None:
        try:
            while True:
                kind, message = self.events.get_nowait()
                if kind == "status":
                    self._set_status(message)
                elif kind == "log" and self.state == "running":
                    self._set_status(message[:80])
                elif kind == "finished":
                    self._restore_mouse_precision(reset=True)
                    self.state = "finished"
                    self._set_button_text("开始")
                    self.user32.EnableWindow(self.button_hwnd, True)
                    self._set_status(f"已结束: {message}")
                elif kind == "error":
                    self._restore_mouse_precision(reset=True)
                    self.state = "error"
                    self._set_button_text("开始")
                    self.user32.EnableWindow(self.button_hwnd, True)
                    self._set_status("发生错误")
                    self.user32.MessageBoxW(self.hwnd, message, "开包脚本错误", MB_ICONERROR)
                elif kind == "restore_mouse":
                    self._restore_mouse_precision(reset=True)
        except queue.Empty:
            pass

        if self.close_requested and self.worker and not self.worker.is_alive():
            self.user32.DestroyWindow(self.hwnd)

    def _run_script(self) -> None:
        try:
            config = load_config(self.options.config)
            logger = setup_script_logger(
                script_name="open_package_gui",
                log_dir=config.app.log_dir,
                level=config.app.log_level,
                console=False,
            )
            gui_handler = QueueStatusHandler(self.events)
            gui_handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(gui_handler)

            if self.controller:
                self.controller.logger = logger
            if self.mouse_precision:
                self.mouse_precision.logger = logger

            self.events.put(("status", "运行中"))
            runner = create_runner(
                config=config,
                dry_run=self.options.dry_run,
                skip_delays=self.options.skip_delays,
                logger=logger,
                control=self.controller,
            )
            result = runner.run()
            self.events.put(("finished", result.exit_reason))
        except Exception as exc:
            self.events.put(("error", f"{exc.__class__.__name__}: {exc}"))
        finally:
            self.events.put(("restore_mouse", ""))

    def _register_window_class(self) -> None:
        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self._wnd_proc
        wndclass.hInstance = self.hinstance
        wndclass.hCursor = self.user32.LoadCursorW(None, IDC_ARROW)
        wndclass.hbrBackground = COLOR_WINDOW + 1
        wndclass.lpszClassName = self.class_name
        atom = self.user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom and ctypes.get_last_error() != ERROR_CLASS_ALREADY_EXISTS:
            raise ctypes.WinError(ctypes.get_last_error())

    def _register_hotkey(self) -> None:
        if self.user32.RegisterHotKey(self.hwnd, HOTKEY_ID_TOGGLE, 0, VK_F10):
            self.hotkey_registered = True
            return
        self.hotkey_registered = False
        self.user32.MessageBoxW(
            self.hwnd,
            "F10 全局快捷键注册失败，可能已被其他程序占用。仍可点击按钮控制。",
            "快捷键不可用",
            MB_ICONWARNING,
        )

    def _unregister_hotkey(self) -> None:
        if self.hotkey_registered:
            self.user32.UnregisterHotKey(self.hwnd, HOTKEY_ID_TOGGLE)
            self.hotkey_registered = False

    def _prepare_mouse_precision(self) -> bool:
        if self.options.dry_run:
            return True
        try:
            if self.mouse_precision is None:
                self.mouse_precision = MousePointerPrecisionManager()
            self.mouse_precision.disable_temporarily()
            return True
        except Exception as exc:
            self._set_status("鼠标设置失败")
            self.user32.MessageBoxW(
                self.hwnd,
                f"无法关闭“提高指针精确度”：{exc}",
                "鼠标设置错误",
                MB_ICONERROR,
            )
            return False

    def _restore_mouse_precision(self, *, reset: bool = False) -> None:
        if self.options.dry_run or self.mouse_precision is None:
            return
        try:
            self.mouse_precision.restore()
            if reset:
                self.mouse_precision = None
        except Exception as exc:
            self.user32.MessageBoxW(
                self.hwnd,
                f"无法恢复“提高指针精确度”：{exc}",
                "鼠标设置错误",
                MB_ICONERROR,
            )

    def _configure_win32_api(self) -> None:
        self.kernel32.GetModuleHandleW.restype = HMODULE
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

        self.user32.LoadCursorW.restype = HCURSOR
        self.user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, LPVOID]
        self.user32.RegisterClassW.restype = ATOM
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            HMENU,
            wintypes.HINSTANCE,
            LPVOID,
        ]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UpdateWindow.argtypes = [wintypes.HWND]
        self.user32.SetTimer.argtypes = [wintypes.HWND, UINT_PTR, wintypes.UINT, LPVOID]
        self.user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        self.user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.user32.MessageBoxW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.UINT,
        ]
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]
        self.user32.PostQuitMessage.argtypes = [ctypes.c_int]

    def _create_main_window(self) -> wintypes.HWND:
        hwnd = self.user32.CreateWindowExW(
            0,
            self.class_name,
            "MXDScript 开包测试",
            WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            376,
            220,
            None,
            None,
            self.hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    def _create_child(
        self,
        *,
        class_name: str,
        text: str,
        style: int,
        x: int,
        y: int,
        width: int,
        height: int,
        control_id: int,
    ) -> wintypes.HWND:
        hwnd = self.user32.CreateWindowExW(
            0,
            class_name,
            text,
            style,
            x,
            y,
            width,
            height,
            self.hwnd,
            HMENU(control_id),
            self.hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return hwnd

    def _handle_message(
        self,
        hwnd: wintypes.HWND,
        msg: int,
        wparam: wintypes.WPARAM,
        lparam: wintypes.LPARAM,
    ) -> int:
        if msg == WM_COMMAND:
            command_id = int(wparam) & 0xFFFF
            notification = (int(wparam) >> 16) & 0xFFFF
            if command_id == self.BUTTON_ID and notification == BN_CLICKED:
                self.toggle()
                return 0
        if msg == WM_TIMER:
            if int(wparam) == self.TIMER_ID:
                self.poll_events()
                return 0
        if msg == WM_HOTKEY:
            if int(wparam) == HOTKEY_ID_TOGGLE:
                self.toggle()
                return 0
        if msg == WM_CLOSE:
            self.close()
            return 0
        if msg == WM_DESTROY:
            self._restore_mouse_precision(reset=True)
            self._unregister_hotkey()
            self.user32.KillTimer(hwnd, self.TIMER_ID)
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _set_button_text(self, text: str) -> None:
        self.user32.SetWindowTextW(self.button_hwnd, text)

    def _set_status(self, text: str) -> None:
        self.user32.SetWindowTextW(self.status_hwnd, text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenPackage one-button GUI")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Do not control YiJianShu hardware")
    parser.add_argument("--skip-delays", action="store_true")
    args = parser.parse_args(argv)

    app = NativeOpenPackageGui(
        GuiOptions(config=args.config, dry_run=args.dry_run, skip_delays=args.skip_delays)
    )
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
