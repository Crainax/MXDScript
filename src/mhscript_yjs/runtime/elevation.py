from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def is_running_as_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_admin_or_relaunch(argv: list[str] | None = None) -> bool:
    if sys.platform != "win32" or os.environ.get("MXDSCRIPT_SKIP_ADMIN") == "1":
        return False
    if is_running_as_admin():
        return False

    relaunch_as_admin(argv)
    return True


def relaunch_as_admin(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    executable = sys.executable
    parameters = _admin_parameters(argv)

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        executable,
        parameters,
        str(Path.cwd()),
        1,
    )
    if result <= 32:
        raise RuntimeError(f"管理员权限启动被取消或失败，ShellExecuteW 返回 {result}。")


def _admin_parameters(argv: list[str]) -> str:
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline(argv)
    return subprocess.list2cmdline([sys.argv[0], *argv])
