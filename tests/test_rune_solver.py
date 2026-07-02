from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import numpy as np

from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.scripts.tool.rune_debug import RunePrediction, SlotPrediction
from mhscript_yjs.scripts.tool.rune_solver import RuneSolver, RuneSolverConfig
from mhscript_yjs.vision.types import Region
from mhscript_yjs.windows.maple import WindowInfo


class RuneSolverTests(unittest.TestCase):
    def test_default_max_attempts_is_five(self) -> None:
        self.assertEqual(RuneSolverConfig().max_attempts, 5)

    def test_recognizes_once_and_presses_frozen_sequence(self) -> None:
        device = DryRunDevice()
        recognizer = FakeRecognizer(
            _prediction(("up", "down", "left", "right"), score=1.25, confidence=0.72)
        )
        solver = RuneSolver(
            device=device,
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.rune_solver"),
            capture=FakeCapture(),
            recognizer=recognizer,
            config=RuneSolverConfig(key_interval_min_ms=0, key_interval_max_ms=0),
        )

        result = solver.trigger_and_press(_window(), attempt=1)

        self.assertTrue(result.pressed)
        self.assertEqual(recognizer.calls, 1)
        self.assertEqual(result.directions, ("up", "down", "left", "right"))
        press_keys = [action.args[0] for action in device.actions if action.name == "press_key"]
        self.assertEqual(
            press_keys,
            [
                keycode("PageDown"),
                keycode("Up"),
                keycode("Down"),
                keycode("Left"),
                keycode("Right"),
            ],
        )

    def test_polls_until_ui_appears_and_presses_early(self) -> None:
        device = DryRunDevice()
        recognizer = SequenceRecognizer(
            [
                _prediction(
                    ("up", "down", "left", "right"),
                    score=0.10,
                    confidence=0.10,
                    selection_score=0.10,
                ),
                _prediction(("right", "left", "down", "up"), score=1.25, confidence=0.72),
            ]
        )
        solver = RuneSolver(
            device=device,
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.rune_solver"),
            capture=FakeCapture(),
            recognizer=recognizer,
            config=RuneSolverConfig(key_interval_min_ms=0, key_interval_max_ms=0),
        )

        result = solver.trigger_and_press(_window(), attempt=1)

        self.assertTrue(result.pressed)
        self.assertEqual(recognizer.calls, 2)
        self.assertEqual(result.directions, ("right", "left", "down", "up"))
        press_keys = [action.args[0] for action in device.actions if action.name == "press_key"]
        self.assertEqual(
            press_keys,
            [
                keycode("PageDown"),
                keycode("Right"),
                keycode("Left"),
                keycode("Down"),
                keycode("Up"),
            ],
        )

    def test_low_confidence_saves_screenshot_and_exits_ui(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            device = DryRunDevice()
            solver = RuneSolver(
                device=device,
                sleeper=NullSleeper(),
                logger=logging.getLogger("test.rune_solver"),
                capture=FakeCapture(),
                recognizer=FakeRecognizer(
                    _prediction(
                        ("up", "down", "left", "right"),
                        score=1.25,
                        confidence=0.72,
                        low_slot=1,
                    )
                ),
                config=RuneSolverConfig(
                    retry_delay_ms=0,
                    failure_screenshot_dir=Path(directory),
                ),
            )

            result = solver.trigger_and_press(_window(), attempt=3)

            self.assertFalse(result.pressed)
            self.assertIn("槽位置信度过低", result.reason)
            self.assertIsNotNone(result.screenshot_path)
            assert result.screenshot_path is not None
            self.assertTrue(result.screenshot_path.exists())
            self.assertEqual(result.screenshot_path.parent, Path(directory))
            press_keys = [action.args[0] for action in device.actions if action.name == "press_key"]
            self.assertEqual(press_keys, [keycode("PageDown"), keycode("Space"), keycode("Space")])

    def test_missing_ui_returns_ui_missing_without_space_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            device = DryRunDevice()
            solver = RuneSolver(
                device=device,
                sleeper=NullSleeper(),
                logger=logging.getLogger("test.rune_solver"),
                capture=FakeCapture(),
                recognizer=FakeRecognizer(
                    _prediction(
                        ("up", "down", "left", "right"),
                        score=0.10,
                        confidence=0.10,
                        selection_score=0.10,
                    )
                ),
                config=RuneSolverConfig(
                    failure_screenshot_dir=Path(directory),
                ),
            )

            result = solver.trigger_and_press(_window(), attempt=2)

            self.assertEqual(result.status, "ui_missing")
            self.assertFalse(result.pressed)
            self.assertIn("rune_ui_missing", result.reason)
            self.assertIsNotNone(result.screenshot_path)
            self.assertEqual(solver.recognizer.calls, 6)  # type: ignore[attr-defined]
            press_keys = [action.args[0] for action in device.actions if action.name == "press_key"]
            self.assertEqual(press_keys, [keycode("PageDown")])


class FakeCapture:
    def capture_region(self, region: Region) -> np.ndarray:
        return np.zeros((region.height, region.width, 3), dtype=np.uint8)


class FakeRecognizer:
    def __init__(self, prediction: RunePrediction) -> None:
        self.prediction = prediction
        self.calls = 0

    def recognize(self, image: np.ndarray) -> RunePrediction:
        self.calls += 1
        return self.prediction


class SequenceRecognizer:
    def __init__(self, predictions: list[RunePrediction]) -> None:
        self.predictions = predictions
        self.calls = 0

    def recognize(self, image: np.ndarray) -> RunePrediction:
        self.calls += 1
        index = min(self.calls - 1, len(self.predictions) - 1)
        return self.predictions[index]


def _prediction(
    directions: tuple[str, str, str, str],
    *,
    score: float,
    confidence: float,
    low_slot: int | None = None,
    selection_score: float | None = None,
) -> RunePrediction:
    slots = tuple(
        SlotPrediction(
            slot=index,
            slot_x=550.0 + index * 92,
            predicted=direction,
            confidence=0.35 if low_slot == index else confidence,
            raw_score=confidence,
            match_x=550 + index * 92,
            match_y=240,
            all_scores={direction: confidence},
        )
        for index, direction in enumerate(directions)
    )
    return RunePrediction(
        predicted=directions,
        group_center_x=690.0,
        score=score,
        slots=slots,  # type: ignore[arg-type]
        selection_score=selection_score,
    )


def _window() -> WindowInfo:
    return WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600)


if __name__ == "__main__":
    unittest.main()
