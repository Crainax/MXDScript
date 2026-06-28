from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from mhscript_yjs.gui.api import GuiApi


class GuiApiTests(unittest.TestCase):
    def test_get_state_uses_appdata_and_exposes_scripts(self) -> None:
        with _temporary_local_appdata() as appdata:
            state = GuiApi().get_state()

        self.assertTrue(state["ok"])
        self.assertEqual(state["app"]["title"], "MXD脚本库")
        self.assertEqual(state["runtime"]["logDir"], str(appdata / "MXDScriptLibrary" / "logs"))
        self.assertGreaterEqual(len(state["runtime"]["scripts"]), 4)

    def test_save_shortcuts_rejects_escape(self) -> None:
        with _temporary_local_appdata():
            response = GuiApi().save_shortcuts({"open_package": "Esc"})

        self.assertFalse(response["ok"])
        self.assertIn("Esc", response["error"])

    def test_save_run_options_persists_hotkey_runtime_mode(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_run_options(dry_run=True, skip_delays=True)
            state = api.get_state()

        self.assertTrue(response["ok"])
        self.assertTrue(state["settings"]["dryRun"])
        self.assertTrue(state["settings"]["skipDelays"])

    def test_placeholder_script_emits_log_events(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.start_script(
                "event_placeholder",
                {"dryRun": True, "skipDelays": True},
            )
            self.assertTrue(response["ok"])

            for _ in range(20):
                state = api.get_state()["runtime"]
                script = next(
                    item for item in state["scripts"] if item["id"] == "event_placeholder"
                )
                if script["status"] == "finished":
                    break
                time.sleep(0.01)
            else:
                self.fail("placeholder script did not finish")

            events = api.poll_events()["events"]

        self.assertTrue(any(event["type"] == "log" for event in events))
        self.assertTrue(any(event["type"] == "finished" for event in events))


class _temporary_local_appdata:
    def __init__(self) -> None:
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._old_value: str | None = None
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_value = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._tempdir.name
        self.path = Path(self._tempdir.name)
        return self.path

    def __exit__(self, *args: object) -> None:
        if self._old_value is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old_value
        if self._tempdir is not None:
            self._tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
