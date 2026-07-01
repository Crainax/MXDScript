from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from mhscript_yjs.scripts.tool.rune_capture import (
    _coerce_capture_interval,
    _payload_for_capture,
)
from mhscript_yjs.scripts.tool.rune_debug import RunePrediction, SlotPrediction


class RuneCaptureTests(unittest.TestCase):
    def test_capture_interval_is_clamped_for_sampling_tool(self) -> None:
        self.assertEqual(_coerce_capture_interval("bad"), 5.0)
        self.assertEqual(_coerce_capture_interval(0.01), 0.5)
        self.assertEqual(_coerce_capture_interval(9999), 3600.0)
        self.assertEqual(_coerce_capture_interval(5), 5.0)

    def test_payload_contains_four_crops_and_labels(self) -> None:
        image = np.zeros((768, 1366, 3), dtype=np.uint8)
        prediction = RunePrediction(
            predicted=("up", "down", "left", "right"),
            group_center_x=690.0,
            score=2.0,
            slots=(
                _slot(0, 552.0, "up"),
                _slot(1, 644.0, "down"),
                _slot(2, 736.0, "left"),
                _slot(3, 828.0, "right"),
            ),
        )

        payload = _payload_for_capture(
            image=image,
            prediction=prediction,
            screenshot_path=Path(r"D:\RuneSamples\rune_capture.png"),
            output_dir=Path(r"D:\RuneSamples"),
            interval_seconds=5.0,
            saved_count=1,
        )

        slots = payload["slots"]
        self.assertEqual(payload["kind"], "runeCapture")
        self.assertEqual(payload["savedCount"], 1)
        self.assertEqual([slot["label"] for slot in slots], ["上", "下", "左", "右"])
        self.assertTrue(
            all(str(slot["cropDataUrl"]).startswith("data:image/png;base64,") for slot in slots)
        )


def _slot(slot: int, slot_x: float, direction: str) -> SlotPrediction:
    return SlotPrediction(
        slot=slot,
        slot_x=slot_x,
        predicted=direction,
        confidence=0.5,
        raw_score=0.5,
        match_x=int(slot_x),
        match_y=240,
        all_scores={direction: 0.5},
    )


if __name__ == "__main__":
    unittest.main()
