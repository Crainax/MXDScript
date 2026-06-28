from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from mhscript_yjs.runtime.logging import (
    close_logger_handlers,
    logger_file_path,
    setup_script_logger,
)
from mhscript_yjs.runtime.script_manager import ScriptManager


class ScriptManagerTests(unittest.TestCase):
    def test_snapshot_exposes_script_library_items(self) -> None:
        with tempfile.TemporaryDirectory() as appdata:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = appdata
            try:
                snapshot = ScriptManager().snapshot()
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old

        scripts = snapshot["scripts"]
        self.assertGreaterEqual(len(scripts), 4)
        self.assertEqual(scripts[0]["id"], "open_package")
        self.assertEqual(scripts[0]["name"], "自动开包")
        self.assertEqual(scripts[0]["defaultShortcut"], "F10")
        self.assertFalse(scripts[0]["placeholder"])
        self.assertTrue(any(script["placeholder"] for script in scripts[1:]))

    def test_script_logger_uses_unique_file_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory)
            first = setup_script_logger(script_name="sample", log_dir=log_dir, console=False)
            first_path = logger_file_path(first)
            second = setup_script_logger(script_name="sample", log_dir=log_dir, console=False)
            second_path = logger_file_path(second)
            close_logger_handlers(second)

        self.assertIsNotNone(first_path)
        self.assertIsNotNone(second_path)
        self.assertNotEqual(first_path, second_path)


if __name__ == "__main__":
    unittest.main()
