from __future__ import annotations

import argparse

from mhscript_yjs.gui import web_app
from mhscript_yjs.scripts.tool import open_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mhscript-yjs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    open_package_parser = subparsers.add_parser(
        "open-package",
        help="Run the Python migration of Tool/open_package.km",
    )
    open_package_parser.add_argument("--config", default=None)
    open_package_parser.add_argument("--live", action="store_true")
    open_package_parser.add_argument("--skip-delays", action="store_true")
    open_package_parser.add_argument("--max-iterations", type=int, default=None)

    gui_parser = subparsers.add_parser(
        "gui",
        help="Run the MXD script library GUI.",
    )
    gui_parser.add_argument("--dev-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "open-package":
        child_args: list[str] = []
        if args.config:
            child_args.extend(["--config", args.config])
        if args.live:
            child_args.append("--live")
        if args.skip_delays:
            child_args.append("--skip-delays")
        if args.max_iterations is not None:
            child_args.extend(["--max-iterations", str(args.max_iterations)])
        return open_package.main(child_args)
    if args.command == "gui":
        child_args = []
        if args.dev_url:
            child_args.extend(["--dev-url", args.dev_url])
        return web_app.main(child_args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
