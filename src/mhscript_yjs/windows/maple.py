from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


class WindowNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


def find_window(fragment: str) -> WindowInfo:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    matches: list[int] = []
    wanted = fragment.lower()

    enum_windows_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_windows_proc
    def enum_proc(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if wanted in title.lower():
            matches.append(hwnd)
            return False
        return True

    user32.EnumWindows(enum_proc, 0)
    if not matches:
        raise WindowNotFoundError(f"Window containing title {fragment!r} was not found")

    hwnd = matches[0]
    return get_client_window_info(hwnd)


def refresh_window_info(previous: WindowInfo | None, fragment: str) -> WindowInfo:
    if previous is not None:
        try:
            return get_client_window_info(previous.hwnd)
        except Exception:
            pass
    return find_window(fragment)


def get_client_window_info(hwnd: int) -> WindowInfo:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    rect = _RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())

    origin = _POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise ctypes.WinError(ctypes.get_last_error())

    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)

    return WindowInfo(
        hwnd=int(hwnd),
        title=buffer.value,
        x=int(origin.x),
        y=int(origin.y),
        width=int(rect.right - rect.left),
        height=int(rect.bottom - rect.top),
    )
