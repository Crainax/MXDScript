from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from mhscript_yjs.scripts.tool.rune_train import locate_panel, parse_expected_sequence


def test_parse_expected_sequence_supports_type_prefix() -> None:
    assert parse_expected_sequence(Path("type92_上上左下.png")) == (
        "up",
        "up",
        "left",
        "down",
    )


def test_parse_expected_sequence_supports_bare_direction_name() -> None:
    assert parse_expected_sequence(Path("左上下右.png")) == (
        "left",
        "up",
        "down",
        "right",
    )


def test_locate_panel_finds_synthetic_capsule() -> None:
    image = np.zeros((768, 1366, 3), dtype=np.uint8)
    x, y, width, height = 500, 205, 400, 82
    _draw_capsule(image, x, y, width, height)
    for fraction, color in zip(
        (0.14, 0.39, 0.62, 0.86),
        ((0, 0, 255), (0, 255, 255), (0, 255, 0), (255, 0, 255)),
        strict=True,
    ):
        center_x = int(x + width * fraction)
        cv2.circle(image, (center_x, y + height // 2), 12, color, -1)

    panel = locate_panel(image)

    assert abs(panel.x - x) <= 12
    assert abs(panel.y - y) <= 12
    assert abs(panel.width - width) <= 25
    assert panel.score > 0.2


def _draw_capsule(image: np.ndarray, x: int, y: int, width: int, height: int) -> None:
    color = (30, 180, 230)
    thickness = 3
    radius = height // 2 - thickness
    center_y = y + height // 2
    cv2.ellipse(
        image,
        (x + height // 2, center_y),
        (height // 2 - thickness, height // 2 - thickness),
        90,
        0,
        180,
        color,
        thickness,
    )
    cv2.ellipse(
        image,
        (x + width - height // 2, center_y),
        (height // 2 - thickness, height // 2 - thickness),
        270,
        0,
        180,
        color,
        thickness,
    )
    cv2.line(
        image,
        (x + height // 2, center_y - radius),
        (x + width - height // 2, center_y - radius),
        color,
        thickness,
    )
    cv2.line(
        image,
        (x + height // 2, center_y + radius),
        (x + width - height // 2, center_y + radius),
        color,
        thickness,
    )
