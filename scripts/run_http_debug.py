from __future__ import annotations

import argparse
import time
from pathlib import Path

from mhscript_yjs.gui.api import GuiApi
from mhscript_yjs.gui.http_server import GuiHttpServer


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the MXDScript GUI HTTP server.")
    parser.add_argument("--static-root", type=Path, default=Path("gui_web/dist"))
    parser.add_argument("--url-file", type=Path, default=Path(".mxd_http_url"))
    parser.add_argument("--enable-hotkeys", action="store_true")
    args = parser.parse_args()

    server = GuiHttpServer(
        static_root=args.static_root,
        api=GuiApi(enable_hotkeys=args.enable_hotkeys),
    )
    server.start()
    args.url_file.write_text(server.url, encoding="utf-8")
    print(server.url, flush=True)
    try:
        while True:
            time.sleep(3600)
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
