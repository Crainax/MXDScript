from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from mhscript_yjs.scripts.tool.rune_debug import RuneCvRecognizer, parse_expected_sequence


def test_parse_expected_sequence_from_filename() -> None:
    assert parse_expected_sequence(Path("type21_\u5de6\u53f3\u4e0b\u4e0a.png")) == (
        "left",
        "right",
        "down",
        "up",
    )
    assert parse_expected_sequence(Path("no_label.png")) is None


def test_recognizer_reads_synthetic_arrow_row() -> None:
    image = np.zeros((768, 1366, 3), dtype=np.uint8)
    center_x = 690
    directions = ("left", "down", "right", "up")
    for offset, direction in zip((-138, -46, 46, 138), directions, strict=True):
        _draw_arrow(image, center_x + offset, 240, direction)

    prediction = RuneCvRecognizer().recognize(image)

    assert prediction.predicted == directions


def _draw_arrow(image: np.ndarray, center_x: int, center_y: int, direction: str) -> None:
    cx = int(center_x)
    cy = int(center_y)
    if direction == "right":
        points = np.array(
            [
                [cx - 20, cy - 9],
                [cx - 4, cy - 9],
                [cx - 4, cy - 16],
                [cx + 20, cy],
                [cx - 4, cy + 16],
                [cx - 4, cy + 9],
                [cx - 20, cy + 9],
            ],
            np.int32,
        )
    elif direction == "left":
        points = np.array(
            [
                [cx + 20, cy - 9],
                [cx + 4, cy - 9],
                [cx + 4, cy - 16],
                [cx - 20, cy],
                [cx + 4, cy + 16],
                [cx + 4, cy + 9],
                [cx + 20, cy + 9],
            ],
            np.int32,
        )
    elif direction == "up":
        points = np.array(
            [
                [cx - 9, cy + 20],
                [cx - 9, cy + 4],
                [cx - 16, cy + 4],
                [cx, cy - 20],
                [cx + 16, cy + 4],
                [cx + 9, cy + 4],
                [cx + 9, cy + 20],
            ],
            np.int32,
        )
    else:
        points = np.array(
            [
                [cx - 9, cy - 20],
                [cx - 9, cy - 4],
                [cx - 16, cy - 4],
                [cx, cy + 20],
                [cx + 16, cy - 4],
                [cx + 9, cy - 4],
                [cx + 9, cy - 20],
            ],
            np.int32,
        )
    cv2.fillPoly(image, [points], (0, 255, 255))
