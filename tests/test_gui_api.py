from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mhscript_yjs.gui.api import GuiApi
from mhscript_yjs.runtime.app_paths import settings_path


class GuiApiTests(unittest.TestCase):
    def test_get_state_uses_appdata_and_exposes_scripts(self) -> None:
        with _temporary_local_appdata() as appdata:
            state = GuiApi().get_state()

        self.assertTrue(state["ok"])
        self.assertEqual(state["app"]["title"], "MXD脚本库")
        self.assertEqual(state["runtime"]["logDir"], str(appdata / "MXDScriptLibrary" / "logs"))
        self.assertEqual(len(state["runtime"]["scripts"]), 7)
        self.assertEqual(state["settings"]["shortcuts"]["leveling"], "Ctrl+F10")
        self.assertEqual(state["settings"]["shortcuts"]["open_package"], "")
        self.assertEqual(state["settings"]["shortcuts"]["rune_capture"], "Ctrl+F7")
        self.assertEqual(
            state["settings"]["scriptOptions"]["rune_capture"]["outputDir"],
            r"protype\RuneInstance",
        )
        self.assertEqual(
            state["settings"]["scriptOptions"]["rune_capture"]["captureIntervalSeconds"],
            5.0,
        )
        self.assertEqual(state["scriptData"]["leveling"]["potionJob"], "lynn")
        self.assertIsNone(state["scriptData"]["leveling"]["potionLastUsedAt"])

    def test_save_shortcuts_rejects_escape(self) -> None:
        with _temporary_local_appdata():
            response = GuiApi().save_shortcuts({"open_package": "Esc"})

        self.assertFalse(response["ok"])
        self.assertIn("Esc", response["error"])

    def test_save_shortcuts_allows_empty_to_disable_hotkey(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_shortcuts({"open_package": ""})
            state = api.get_state()

        self.assertTrue(response["ok"])
        self.assertEqual(state["settings"]["shortcuts"]["open_package"], "")

    def test_legacy_open_package_f10_default_is_migrated_to_empty(self) -> None:
        with _temporary_local_appdata():
            path = settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"shortcuts": {"open_package": "F10"}}),
                encoding="utf-8",
            )
            state = GuiApi().get_state()

        self.assertEqual(state["settings"]["shortcuts"]["open_package"], "")

    def test_duplicate_saved_shortcut_keeps_first_script_and_clears_later_one(self) -> None:
        with _temporary_local_appdata():
            path = settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"shortcuts": {"open_package": "Ctrl+F10"}}),
                encoding="utf-8",
            )
            state = GuiApi().get_state()

        self.assertEqual(state["settings"]["shortcuts"]["leveling"], "Ctrl+F10")
        self.assertEqual(state["settings"]["shortcuts"]["open_package"], "")

    def test_save_run_options_persists_hotkey_runtime_mode(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_run_options(dry_run=True, skip_delays=True)
            state = api.get_state()

        self.assertTrue(response["ok"])
        self.assertTrue(state["settings"]["dryRun"])
        self.assertTrue(state["settings"]["skipDelays"])

    def test_daily_script_options_default_enabled_and_persist(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            state = api.get_state()
            response = api.save_script_options(
                "daily_script",
                {"gugu": False, "matchThreshold": 0.94, "aut7": False},
            )
            next_state = GuiApi().get_state()

        self.assertTrue(state["settings"]["scriptOptions"]["daily_script"]["dailyQuest"])
        self.assertTrue(state["settings"]["scriptOptions"]["daily_script"]["gugu"])
        self.assertTrue(state["settings"]["scriptOptions"]["daily_script"]["aut1"])
        self.assertTrue(state["settings"]["scriptOptions"]["daily_script"]["aut7"])
        self.assertNotIn("matchThreshold", state["settings"]["scriptOptions"]["daily_script"])
        self.assertTrue(response["ok"])
        self.assertFalse(next_state["settings"]["scriptOptions"]["daily_script"]["gugu"])
        self.assertFalse(next_state["settings"]["scriptOptions"]["daily_script"]["aut7"])
        self.assertTrue(next_state["settings"]["scriptOptions"]["daily_script"]["otherDaily"])
        self.assertNotIn("matchThreshold", next_state["settings"]["scriptOptions"]["daily_script"])

    def test_debug_script_options_persist_strings_and_zero_threshold(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_script_options(
                "image_recognition",
                {
                    "imagePath": r"D:\Project\MXDScript\assets\CombineMain\SchedulerUI.png",
                    "matchThreshold": 0,
                    "intervalSeconds": 0.1,
                },
            )
            state = GuiApi().get_state()

        image_options = state["settings"]["scriptOptions"]["image_recognition"]
        self.assertTrue(response["ok"])
        self.assertEqual(
            image_options["imagePath"],
            r"D:\Project\MXDScript\assets\CombineMain\SchedulerUI.png",
        )
        self.assertEqual(image_options["matchThreshold"], 0.0)
        self.assertEqual(image_options["intervalSeconds"], 0.1)

    def test_debug_script_interval_is_clamped_independently_from_threshold(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_script_options(
                "coordinate_detector",
                {"intervalSeconds": 5, "matchThreshold": 2},
            )
            state = GuiApi().get_state()

        detector_options = state["settings"]["scriptOptions"]["coordinate_detector"]
        self.assertTrue(response["ok"])
        self.assertEqual(detector_options["intervalSeconds"], 5.0)
        self.assertEqual(detector_options["matchThreshold"], 1.0)

    def test_coordinate_mover_options_persist_target_and_mode(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_script_options(
                "coordinate_mover",
                {"targetX": "123", "targetY": "45", "moveMode": "Move"},
            )
            state = GuiApi().get_state()

        self.assertTrue(response["ok"])
        self.assertEqual(state["settings"]["scriptOptions"]["coordinate_mover"]["targetX"], "123")
        self.assertEqual(state["settings"]["scriptOptions"]["coordinate_mover"]["targetY"], "45")
        self.assertEqual(state["settings"]["scriptOptions"]["coordinate_mover"]["moveMode"], "Move")

    def test_rune_capture_options_persist_directory_and_interval(self) -> None:
        with _temporary_local_appdata():
            api = GuiApi()
            response = api.save_script_options(
                "rune_capture",
                {"outputDir": r"D:\RuneSamples", "captureIntervalSeconds": 12.5},
            )
            state = GuiApi().get_state()

        rune_options = state["settings"]["scriptOptions"]["rune_capture"]
        self.assertTrue(response["ok"])
        self.assertEqual(rune_options["outputDir"], r"D:\RuneSamples")
        self.assertEqual(rune_options["captureIntervalSeconds"], 12.5)

    def test_select_directory_returns_dialog_path(self) -> None:
        with (
            _temporary_local_appdata(),
            patch("mhscript_yjs.gui.api._select_directory", return_value=Path(r"D:\RuneSamples")),
        ):
            response = GuiApi().select_directory(r"D:\Initial")

        self.assertTrue(response["ok"])
        self.assertEqual(response["path"], r"D:\RuneSamples")


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
