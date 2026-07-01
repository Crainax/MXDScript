from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mhscript_yjs.runtime.logging import (
    close_logger_handlers,
    logger_file_path,
    setup_script_logger,
)
from mhscript_yjs.runtime.script_manager import ScriptManager
from mhscript_yjs.scripts.registry import ScriptDefinition, ScriptRunContext, ScriptRunResult


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
        self.assertEqual(len(scripts), 7)
        self.assertEqual(scripts[0]["id"], "leveling")
        self.assertEqual(scripts[0]["name"], "练级")
        self.assertEqual(scripts[0]["defaultShortcut"], "Ctrl+F10")
        self.assertFalse(scripts[0]["placeholder"])
        self.assertEqual(scripts[1]["id"], "daily_script")
        self.assertFalse(scripts[1]["placeholder"])
        self.assertEqual(scripts[2]["id"], "open_package")
        self.assertEqual(scripts[2]["defaultShortcut"], "")
        self.assertEqual(scripts[3]["id"], "image_recognition")
        self.assertEqual(scripts[3]["defaultShortcut"], "")
        self.assertEqual(scripts[4]["id"], "coordinate_detector")
        self.assertEqual(scripts[5]["id"], "coordinate_mover")
        self.assertEqual(scripts[5]["defaultShortcut"], "")
        self.assertEqual(scripts[5]["defaultOptions"]["moveMode"], "MoveB")
        self.assertEqual(scripts[6]["id"], "rune_capture")
        self.assertEqual(scripts[6]["defaultShortcut"], "Ctrl+F7")
        self.assertEqual(scripts[6]["defaultOptions"]["outputDir"], r"protype\RuneInstance")
        self.assertEqual(scripts[6]["defaultOptions"]["captureIntervalSeconds"], 5.0)
        self.assertEqual(
            scripts[6]["defaultOptions"]["modelPath"],
            r"assets\Rune\rune_template_model.npz",
        )

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

    def test_mouse_precision_tracks_pause_resume_and_stop(self) -> None:
        ready = threading.Event()
        stop_seen = threading.Event()
        finish_allowed = threading.Event()
        fake_mouse = FakeMousePrecision()

        def runner(context: ScriptRunContext) -> ScriptRunResult:
            ready.set()
            while not context.control.stop_requested():
                context.control.wait_if_paused()
                time.sleep(0.01)
            stop_seen.set()
            finish_allowed.wait(timeout=1)
            return ScriptRunResult(exit_reason="stop_requested")

        definition = ScriptDefinition(
            id="long_script",
            name="长运行脚本",
            category="测试",
            description="测试鼠标设置状态切换。",
            module="tests.test_script_manager",
            default_shortcut="F7",
            runner=runner,
            requires_mouse_precision=True,
        )

        with tempfile.TemporaryDirectory() as appdata:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = appdata
            try:
                manager = ScriptManager(
                    (definition,),
                    mouse_precision_factory=lambda logger: fake_mouse,  # type: ignore[arg-type]
                )
                manager.start("long_script", dry_run=False, skip_delays=True)
                self.assertTrue(_wait_until(lambda: ready.is_set()))
                self.assertTrue(_wait_until(lambda: fake_mouse.calls == ["disable"]))

                manager.pause()
                self.assertEqual(fake_mouse.calls, ["disable", "restore"])

                manager.resume()
                self.assertEqual(fake_mouse.calls, ["disable", "restore", "disable"])

                manager.stop()
                self.assertEqual(
                    fake_mouse.calls,
                    ["disable", "restore", "disable", "restore"],
                )
                self.assertTrue(_wait_until(lambda: stop_seen.is_set()))
                finish_allowed.set()
                self.assertTrue(_wait_until(lambda: _status(manager, "long_script") == "finished"))
            finally:
                finish_allowed.set()
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old

    def test_starting_another_script_stops_active_script_first(self) -> None:
        first_ready = threading.Event()
        first_stopped = threading.Event()
        second_ran = threading.Event()

        def first_runner(context: ScriptRunContext) -> ScriptRunResult:
            first_ready.set()
            while not context.control.stop_requested():
                time.sleep(0.01)
            first_stopped.set()
            return ScriptRunResult(exit_reason="stop_requested")

        def second_runner(context: ScriptRunContext) -> ScriptRunResult:
            second_ran.set()
            return ScriptRunResult(exit_reason="completed")

        first = ScriptDefinition(
            id="first",
            name="第一个脚本",
            category="测试",
            description="长运行脚本。",
            module="tests.test_script_manager",
            default_shortcut="F7",
            runner=first_runner,
        )
        second = ScriptDefinition(
            id="second",
            name="第二个脚本",
            category="测试",
            description="短脚本。",
            module="tests.test_script_manager",
            default_shortcut="F8",
            runner=second_runner,
        )

        with tempfile.TemporaryDirectory() as appdata:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = appdata
            try:
                manager = ScriptManager((first, second))
                manager.start("first", skip_delays=True)
                self.assertTrue(_wait_until(lambda: first_ready.is_set()))

                manager.start("second", skip_delays=True)

                self.assertTrue(first_stopped.is_set())
                self.assertTrue(_wait_until(lambda: second_ran.is_set()))
                self.assertTrue(_wait_until(lambda: _status(manager, "second") == "finished"))
                time.sleep(0.1)
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old

    def test_script_can_request_pause_and_resume(self) -> None:
        pause_requested = threading.Event()
        resumed = threading.Event()

        def runner(context: ScriptRunContext) -> ScriptRunResult:
            context.request_pause()
            pause_requested.set()
            context.control.wait_if_paused()
            resumed.set()
            return ScriptRunResult(exit_reason="completed")

        definition = ScriptDefinition(
            id="pausing_script",
            name="Internal pause script",
            category="test",
            description="Tests request_pause from inside a script.",
            module="tests.test_script_manager",
            default_shortcut="F6",
            runner=runner,
        )

        with tempfile.TemporaryDirectory() as appdata:
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = appdata
            try:
                manager = ScriptManager((definition,))
                manager.start("pausing_script", skip_delays=True)
                self.assertTrue(_wait_until(lambda: pause_requested.is_set()))
                self.assertTrue(_wait_until(lambda: _status(manager, "pausing_script") == "paused"))

                manager.resume()

                self.assertTrue(_wait_until(lambda: resumed.is_set()))
                self.assertTrue(
                    _wait_until(lambda: _status(manager, "pausing_script") == "finished")
                )
            finally:
                if old is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old


@dataclass
class FakeMousePrecision:
    calls: list[str] = field(default_factory=list)

    def disable_temporarily(self) -> None:
        self.calls.append("disable")

    def restore(self) -> None:
        self.calls.append("restore")


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _status(manager: ScriptManager, script_id: str) -> str:
    script = next(item for item in manager.snapshot()["scripts"] if item["id"] == script_id)
    return str(script["status"])


if __name__ == "__main__":
    unittest.main()
