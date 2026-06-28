from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from mhscript_yjs.gui.api import GuiApi


class GuiHttpServer:
    def __init__(self, *, static_root: Path, api: GuiApi | None = None) -> None:
        self.static_root = static_root.resolve()
        self.api = api or GuiApi()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mxdscript-gui-http",
            daemon=True,
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/"

    @property
    def api_url(self) -> str:
        return f"{self.url.rstrip('/')}/api"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        static_root = self.static_root
        api = self.api

        class Handler(BaseHTTPRequestHandler):
            server_version = "MXDScriptGui/1.0"

            def do_OPTIONS(self) -> None:
                self._send_json({"ok": True})

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/state":
                    self._send_json(api.get_state())
                    return
                if parsed.path == "/api/events":
                    self._send_json(api.poll_events())
                    return
                self._send_static(parsed.path)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                body = self._read_json_body()

                if parsed.path == "/api/start":
                    script_id = str(body.get("scriptId", ""))
                    options = body.get("options")
                    self._send_json(
                        api.start_script(
                            script_id,
                            options if isinstance(options, dict) else {},
                        )
                    )
                    return
                if parsed.path == "/api/pause":
                    self._send_json(api.pause_script())
                    return
                if parsed.path == "/api/resume":
                    self._send_json(api.resume_script())
                    return
                if parsed.path == "/api/stop":
                    self._send_json(api.stop_script())
                    return
                if parsed.path == "/api/shortcuts":
                    shortcuts = body.get("shortcuts")
                    self._send_json(
                        api.save_shortcuts(
                            {str(key): str(value) for key, value in shortcuts.items()}
                            if isinstance(shortcuts, dict)
                            else {}
                        )
                    )
                    return
                if parsed.path == "/api/open-log-dir":
                    self._send_json(api.open_log_dir())
                    return
                if parsed.path == "/api/open-path":
                    self._send_json(api.open_path(str(body.get("path", ""))))
                    return

                self._send_json(
                    {"ok": False, "error": f"Unknown endpoint: {parsed.path}"},
                    status=HTTPStatus.NOT_FOUND,
                )

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send_static(self, raw_path: str) -> None:
                relative = unquote(raw_path).lstrip("/") or "index.html"
                target = (static_root / relative).resolve()
                if not _is_relative_to(target, static_root) or not target.is_file():
                    target = static_root / "index.html"

                try:
                    data = target.read_bytes()
                except OSError as exc:
                    self._send_json(
                        {"ok": False, "error": str(exc)},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return

                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self._send_common_headers(content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read_json_body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}

            def _send_json(
                self,
                payload: dict[str, Any],
                *,
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self._send_common_headers("application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_common_headers(self, content_type: str) -> None:
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

        return Handler


def add_api_query(url: str, api_url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["api"] = [api_url]
    query_text = urlencode(query, doseq=True)
    return parsed._replace(query=query_text).geturl()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
