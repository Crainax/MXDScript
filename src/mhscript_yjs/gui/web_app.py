from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mhscript_yjs.core.config import project_root
from mhscript_yjs.gui.api import GuiApi
from mhscript_yjs.gui.http_server import GuiHttpServer, add_api_query
from mhscript_yjs.runtime.elevation import ensure_admin_or_relaunch
from mhscript_yjs.runtime.resources import prepare_runtime_resources


def main(argv: list[str] | None = None) -> int:
    if ensure_admin_or_relaunch(argv):
        return 0

    prepare_runtime_resources()

    parser = argparse.ArgumentParser(description="Run the MXD script library GUI.")
    parser.add_argument(
        "--dev-url",
        default=os.environ.get("MXD_GUI_DEV_URL"),
        help="Use a running Vite dev server instead of the bundled frontend.",
    )
    args = parser.parse_args(argv)

    try:
        import webview
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 pywebview，请先安装项目依赖：python -m pip install -e .") from exc

    http_server = GuiHttpServer(static_root=_frontend_dist_dir(), api=GuiApi(enable_hotkeys=True))
    http_server.start()
    url = add_api_query(args.dev_url, http_server.api_url) if args.dev_url else http_server.url
    webview.create_window(
        "MXD脚本库",
        url,
        width=1280,
        height=820,
        min_size=(900, 680),
    )
    try:
        webview.start(debug=bool(args.dev_url))
    finally:
        http_server.stop()
    return 0


def _frontend_index_url() -> str:
    index = _frontend_dist_dir() / "index.html"
    if not index.exists():
        raise RuntimeError(
            "未找到前端构建产物。开发时先运行 gui_web 的 Vite dev server 并传入 --dev-url，"
            "或先执行 npm run build。"
        )
    return index.as_uri()


def _frontend_dist_dir() -> Path:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "gui_web" / "dist")
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        candidates.append(bundle_root / "gui_web" / "dist")
    candidates.append(project_root() / "gui_web" / "dist")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


if __name__ == "__main__":
    raise SystemExit(main())
