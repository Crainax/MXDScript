from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from mhscript_yjs.scripts.tool.rune_debug import (
    RuneCvRecognizer,
    RunePrediction,
    SlotPrediction,
    _should_use_expanded_panel,
    parse_expected_sequence,
)
from mhscript_yjs.scripts.tool.rune_train import PanelCandidate


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


def test_model_panel_switch_requires_upper_panel_and_strong_gain() -> None:
    legacy = PanelCandidate(score=0.7, x=480, y=201, width=400, height=88)
    weak_upper = PanelCandidate(score=1.0, x=500, y=160, width=400, height=88)
    strong_upper = PanelCandidate(score=1.0, x=500, y=160, width=400, height=88)
    lower = PanelCandidate(score=1.0, x=500, y=201, width=400, height=88)

    assert not _should_use_expanded_panel(
        legacy,
        _prediction(selection_score=1.0),
        weak_upper,
        _prediction(selection_score=1.3),
    )
    assert _should_use_expanded_panel(
        legacy,
        _prediction(selection_score=1.0),
        strong_upper,
        _prediction(selection_score=1.6),
    )
    assert not _should_use_expanded_panel(
        legacy,
        _prediction(selection_score=1.0),
        lower,
        _prediction(selection_score=1.8),
    )


def _prediction(selection_score: float) -> RunePrediction:
    return RunePrediction(
        predicted=("up", "down", "left", "right"),
        group_center_x=690.0,
        score=selection_score,
        slots=(
            _slot(0, "up"),
            _slot(1, "down"),
            _slot(2, "left"),
            _slot(3, "right"),
        ),
        selection_score=selection_score,
    )


def _slot(slot: int, direction: str) -> SlotPrediction:
    return SlotPrediction(
        slot=slot,
        slot_x=690.0,
        predicted=direction,
        confidence=0.8,
        raw_score=0.8,
        match_x=690,
        match_y=240,
        all_scores={direction: 0.8},
    )


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
