from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from mhscript_yjs.scripts.tool.rune_train import (
    DIRECTION_NAMES,
    PanelCandidate,
    _apply_review_results_to_slot_records,
    _ImageRecord,
    _load_review_results,
    _SlotRecord,
    _summarize_review_results,
    locate_panel,
    locate_panel_expanded,
    parse_expected_sequence,
)


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


def test_locate_panel_prefers_upper_capsule_with_arrows() -> None:
    image = np.zeros((768, 1366, 3), dtype=np.uint8)
    x, y, width, height = 520, 150, 400, 82
    _draw_capsule(image, x, y, width, height)
    _draw_capsule(image, 680, 220, 400, 82)
    for fraction, color in zip(
        (0.14, 0.39, 0.62, 0.86),
        ((0, 0, 255), (0, 255, 255), (0, 255, 0), (255, 0, 255)),
        strict=True,
    ):
        center_x = int(x + width * fraction)
        cv2.circle(image, (center_x, y + height // 2), 12, color, -1)

    panel = locate_panel_expanded(image)

    assert abs(panel.x - x) <= 12
    assert abs(panel.y - y) <= 12
    assert abs(panel.width - width) <= 25


def test_review_results_filter_bad_and_apply_corrections(tmp_path: Path) -> None:
    review_path = tmp_path / "crop_review_results.csv"
    review_path.write_text(
        "\n".join(
            [
                "id,source_image,slot,expected,corrected_expected,crop_path,review,notes",
                "type1_slot1,type1_左右上下.png,1,left,right,../crops/left/a.png,ok,",
                "type1_slot2,type1_左右上下.png,2,right,,../crops/right/b.png,hard,",
                "type1_slot3,type1_左右上下.png,3,up,,../crops/up/c.png,bad,错右",
                "type1_slot4,type1_左右上下.png,4,down,,../crops/down/d.png,bad,裁偏内容无",
            ]
        ),
        encoding="utf-8-sig",
    )
    review_items = _load_review_results(review_path)
    review_by_slot = {
        (item.source_image, item.slot): item
        for item in review_items
    }
    slot_records = [
        _SlotRecord(
            image_index=0,
            slot=index,
            expected_index=0,
            image_path=Path("type1_左右上下.png"),
            reps={},
        )
        for index in range(4)
    ]

    clean_records, clean_keys = _apply_review_results_to_slot_records(
        slot_records,
        review_by_slot,
    )

    assert len(clean_records) == 2
    assert clean_keys == {("type1_左右上下.png", 0), ("type1_左右上下.png", 1)}
    assert DIRECTION_NAMES[clean_records[0].expected_index] == "right"
    assert DIRECTION_NAMES[clean_records[1].expected_index] == "right"

    image_records = [
        _ImageRecord(
            index=0,
            path=Path("type1_左右上下.png"),
            image=np.zeros((10, 10, 3), dtype=np.uint8),
            expected=("left", "right", "up", "down"),
            expected_indices=(0, 3, 0, 1),
            panel=PanelCandidate(1.0, 1, 2, 3, 4),
        )
    ]
    summary = _summarize_review_results(
        review_results=review_path,
        image_records=image_records,
        all_slot_records_count=4,
        clean_slot_records=clean_records,
        review_items=review_items,
        clean_slot_keys=clean_keys,
    )

    assert summary.ok_count == 1
    assert summary.hard_count == 1
    assert summary.bad_count == 2
    assert summary.label_correction_count == 1
    assert summary.clean_training_sample_count == 2
    assert summary.bad_with_direction_count == 1
    assert summary.bad_without_direction_count == 1


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
