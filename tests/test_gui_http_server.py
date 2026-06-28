from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

from mhscript_yjs.gui.http_server import GuiHttpServer, add_api_query


class GuiHttpServerTests(unittest.TestCase):
    def test_state_endpoint_and_static_index(self) -> None:
        with tempfile.TemporaryDirectory() as appdata, tempfile.TemporaryDirectory() as static:
            old_appdata = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = appdata
            static_root = Path(static)
            (static_root / "index.html").write_text("<html>ok</html>", encoding="utf-8")
            server = GuiHttpServer(static_root=static_root)
            server.start()
            try:
                state = _get_json(f"{server.api_url}/state")
                html = urlopen(server.url, timeout=5).read().decode("utf-8")
            finally:
                server.stop()
                if old_appdata is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_appdata

        self.assertTrue(state["ok"])
        self.assertEqual(state["app"]["title"], "MXD脚本库")
        self.assertEqual(html, "<html>ok</html>")

    def test_add_api_query_encodes_api_url(self) -> None:
        url = add_api_query("http://127.0.0.1:1420/?mode=dev", "http://127.0.0.1:51234/api")

        self.assertIn("mode=dev", url)
        self.assertIn("api=http%3A%2F%2F127.0.0.1%3A51234%2Fapi", url)


def _get_json(url: str) -> dict[str, object]:
    return json.loads(urlopen(url, timeout=5).read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
