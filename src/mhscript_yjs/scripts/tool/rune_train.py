from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

DIRECTION_LABELS = {
    "上": "up",
    "下": "down",
    "左": "left",
    "右": "right",
}
DIRECTION_NAMES = ("up", "down", "left", "right")
DIRECTION_KEYS = {
    "up": "U",
    "down": "D",
    "left": "L",
    "right": "R",
    "unknown": "?",
}
SLOT_FRACTIONS = (0.14, 0.39, 0.62, 0.86)
MODEL_VARIANTS = ((132, 36), (132, 40), (152, 36), (152, 40), (112, 36))
DEFAULT_UNKNOWN_THRESHOLD = 1.03


@dataclass(frozen=True)
class PanelCandidate:
    score: float
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class SlotPrediction:
    slot: int
    predicted: str
    expected: str | None
    correct: bool | None
    margin: float
    top_score: float
    scores: dict[str, float]


@dataclass(frozen=True)
class ImagePrediction:
    image: str
    expected: tuple[str, str, str, str] | None
    predicted: tuple[str, str, str, str]
    accepted: bool
    panel: PanelCandidate
    acceptance_score: float
    margin_mean: float
    top_score_mean: float
    correct_count: int | None
    sequence_correct: bool | None
    slots: tuple[SlotPrediction, SlotPrediction, SlotPrediction, SlotPrediction]


@dataclass(frozen=True)
class FoldReport:
    fold: int
    image_count: int
    arrow_accuracy: float
    sequence_accuracy: float


@dataclass(frozen=True)
class TrainingReport:
    positive_dir: str
    negative_dir: str | None
    output_dir: str
    positive_count: int
    negative_count: int
    crop_count: int
    unknown_threshold: float
    cross_validation_arrow_accuracy: float
    cross_validation_sequence_accuracy: float
    final_positive_sequence_accuracy: float
    final_positive_accept_rate: float
    no_rune_ui_reject_rate: float | None
    confusion_matrix: dict[str, dict[str, int]]
    folds: list[FoldReport]
    positive_results: list[ImagePrediction]
    negative_results: list[ImagePrediction]


@dataclass(frozen=True)
class _SlotRecord:
    image_index: int
    slot: int
    expected_index: int
    image_path: Path
    reps: dict[tuple[int, int], np.ndarray]


@dataclass(frozen=True)
class _ImageRecord:
    index: int
    path: Path
    image: np.ndarray
    expected: tuple[str, str, str, str]
    expected_indices: tuple[int, int, int, int]
    panel: PanelCandidate


class RuneTemplateModel:
    def __init__(self, templates: dict[tuple[int, int], dict[int, np.ndarray]]) -> None:
        self.templates = templates

    @classmethod
    def train(
        cls,
        records: list[_SlotRecord],
        train_image_indices: set[int] | None = None,
    ) -> RuneTemplateModel:
        templates: dict[tuple[int, int], dict[int, np.ndarray]] = {}
        for variant in MODEL_VARIANTS:
            big_size, small_size = variant
            samples = [
                (record.expected_index, record.reps[variant])
                for record in records
                if train_image_indices is None or record.image_index in train_image_indices
            ]
            templates[variant] = _train_variant_templates(samples, small_size)
        return cls(templates)

    def predict_reps(
        self,
        reps: dict[tuple[int, int], np.ndarray],
    ) -> tuple[int, float, float, dict[int, float], tuple[int, int, int]]:
        scores = np.zeros(len(DIRECTION_NAMES), np.float32)
        best_locations: dict[int, tuple[int, int, int]] = {}
        for variant, templates in self.templates.items():
            for direction_index, template in templates.items():
                result = cv2.matchTemplate(reps[variant], template, cv2.TM_CCOEFF_NORMED)
                _, max_value, _, max_location = cv2.minMaxLoc(result)
                scores[direction_index] += float(max_value)
                if direction_index not in best_locations or max_value > scores[direction_index]:
                    best_locations[direction_index] = (
                        int(max_location[0]),
                        int(max_location[1]),
                        int(variant[1]),
                    )

        order = np.argsort(scores)[::-1]
        predicted_index = int(order[0])
        margin = float(scores[order[0]] - scores[order[1]])
        top_score = float(scores[order[0]] / max(1, len(self.templates)))
        return predicted_index, margin, top_score, {
            DIRECTION_NAMES[index]: float(score / max(1, len(self.templates)))
            for index, score in enumerate(scores)
        }, best_locations.get(predicted_index, (0, 0, MODEL_VARIANTS[0][1]))

    def save(self, output_path: Path, threshold: float) -> None:
        arrays: dict[str, np.ndarray] = {
            "variants": np.array(MODEL_VARIANTS, dtype=np.int32),
            "unknown_threshold": np.array([threshold], dtype=np.float32),
        }
        for variant, templates in self.templates.items():
            big_size, small_size = variant
            for direction_index, template in templates.items():
                arrays[f"template_{big_size}_{small_size}_{direction_index}"] = template
        output_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(output_path, **arrays)


def parse_expected_sequence(path: Path) -> tuple[str, str, str, str] | None:
    suffix = path.stem.rsplit("_", 1)[-1]
    if len(suffix) != 4:
        return None
    directions: list[str] = []
    for char in suffix:
        direction = DIRECTION_LABELS.get(char)
        if direction is None:
            return None
        directions.append(direction)
    return tuple(directions)  # type: ignore[return-value]


def run_training(
    positive_dir: Path,
    output_dir: Path,
    negative_dir: Path | None = None,
    folds: int = 5,
) -> TrainingReport:
    positive_dir = positive_dir.resolve()
    output_dir = output_dir.resolve()
    negative_dir = negative_dir.resolve() if negative_dir is not None else None
    output_dir.mkdir(parents=True, exist_ok=True)

    image_records = _load_positive_records(positive_dir)
    slot_records = _build_slot_records(image_records)
    fold_reports, cv_results, confusion = _cross_validate(image_records, slot_records, folds)

    final_model = RuneTemplateModel.train(slot_records)
    final_positive_results = [
        _predict_positive_image(record, final_model, DEFAULT_UNKNOWN_THRESHOLD)
        for record in image_records
    ]

    negative_results: list[ImagePrediction] = []
    if negative_dir is not None and negative_dir.exists():
        negative_results = [
            _predict_unlabeled_image(path, final_model, DEFAULT_UNKNOWN_THRESHOLD)
            for path in _iter_image_paths(negative_dir)
        ]

    threshold = _calibrate_threshold(final_positive_results, negative_results)
    final_positive_results = [
        _replace_acceptance(result, result.acceptance_score >= threshold)
        for result in final_positive_results
    ]
    negative_results = [
        _replace_acceptance(result, result.acceptance_score >= threshold)
        for result in negative_results
    ]

    final_model.save(output_dir / "model" / "rune_template_model.npz", threshold)
    _write_crops_and_contact_sheets(image_records, final_model, output_dir)
    _write_debug_images(final_positive_results, negative_results, output_dir)

    cv_arrow_accuracy = _mean([report.arrow_accuracy for report in fold_reports])
    cv_sequence_accuracy = _mean([report.sequence_accuracy for report in fold_reports])
    final_sequence_accuracy = _ratio(
        sum(1 for result in final_positive_results if result.sequence_correct),
        len(final_positive_results),
    )
    final_accept_rate = _ratio(
        sum(1 for result in final_positive_results if result.accepted),
        len(final_positive_results),
    )
    no_rune_ui_reject_rate = (
        _ratio(sum(1 for result in negative_results if not result.accepted), len(negative_results))
        if negative_results
        else None
    )

    report = TrainingReport(
        positive_dir=str(positive_dir),
        negative_dir=str(negative_dir) if negative_dir is not None else None,
        output_dir=str(output_dir),
        positive_count=len(image_records),
        negative_count=len(negative_results),
        crop_count=len(slot_records),
        unknown_threshold=threshold,
        cross_validation_arrow_accuracy=cv_arrow_accuracy,
        cross_validation_sequence_accuracy=cv_sequence_accuracy,
        final_positive_sequence_accuracy=final_sequence_accuracy,
        final_positive_accept_rate=final_accept_rate,
        no_rune_ui_reject_rate=no_rune_ui_reject_rate,
        confusion_matrix=confusion,
        folds=fold_reports,
        positive_results=cv_results,
        negative_results=negative_results,
    )
    _write_report_files(report, output_dir)
    return report


def locate_panel(image: np.ndarray) -> PanelCandidate:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    roi_x, roi_y = 350, 175
    roi = hsv[roi_y:320, roi_x:1050]
    bgr_roi = image[roi_y:320, roi_x:1050]
    hue_mask = cv2.inRange(roi, np.array([12, 20, 80]), np.array([55, 255, 255]))
    gold_mask = cv2.inRange(bgr_roi, np.array([0, 70, 70]), np.array([170, 255, 255]))
    mask = cv2.bitwise_and(hue_mask, gold_mask)

    best: PanelCandidate | None = None
    for width in (360, 380, 400, 420, 440):
        for height in (72, 80, 88):
            template = _capsule_template(width, height)
            result = cv2.matchTemplate(mask, template, cv2.TM_CCORR_NORMED)
            search = result[10:50, :]
            _, template_score, _, location = cv2.minMaxLoc(search)
            x = int(location[0] + roi_x)
            y = int(location[1] + roi_y + 10)
            arrow_energy = _slot_arrow_energy(hsv, x, y, width, height)
            score = float(template_score + 0.12 * arrow_energy)
            candidate = PanelCandidate(score=score, x=x, y=y, width=width, height=height)
            if best is None or candidate.score > best.score:
                best = candidate

    if best is None:
        return PanelCandidate(score=0.0, x=0, y=0, width=0, height=0)
    return best


def _load_positive_records(positive_dir: Path) -> list[_ImageRecord]:
    records: list[_ImageRecord] = []
    for index, path in enumerate(_iter_image_paths(positive_dir)):
        expected = parse_expected_sequence(path)
        if expected is None:
            raise ValueError(f"Image file name does not contain four direction labels: {path}")
        image = _read_image(path)
        panel = locate_panel(image)
        expected_indices = tuple(DIRECTION_NAMES.index(item) for item in expected)
        records.append(
            _ImageRecord(
                index=index,
                path=path,
                image=image,
                expected=expected,
                expected_indices=expected_indices,  # type: ignore[arg-type]
                panel=panel,
            )
        )
    return records


def _build_slot_records(image_records: list[_ImageRecord]) -> list[_SlotRecord]:
    records: list[_SlotRecord] = []
    for image_record in image_records:
        for slot, expected_index in enumerate(image_record.expected_indices):
            reps = {
                variant: _represent(
                    _slot_crop(image_record.image, image_record.panel, slot, variant[0])
                )
                for variant in MODEL_VARIANTS
            }
            records.append(
                _SlotRecord(
                    image_index=image_record.index,
                    slot=slot,
                    expected_index=expected_index,
                    image_path=image_record.path,
                    reps=reps,
                )
            )
    return records


def _train_variant_templates(
    samples: list[tuple[int, np.ndarray]],
    small_size: int,
) -> dict[int, np.ndarray]:
    templates = {
        direction_index: np.mean(
            [
                _center_crop_repr(representation, small_size)
                for label, representation in samples
                if label == direction_index
            ],
            axis=0,
        ).astype(np.float32)
        for direction_index in range(len(DIRECTION_NAMES))
    }

    for _ in range(3):
        aligned: list[tuple[int, np.ndarray]] = []
        for label, representation in samples:
            result = cv2.matchTemplate(
                representation,
                templates[label],
                cv2.TM_CCOEFF_NORMED,
            )
            _, _, _, location = cv2.minMaxLoc(result)
            x, y = location
            aligned.append((label, representation[y : y + small_size, x : x + small_size]))
        templates = {
            direction_index: np.mean(
                [crop for label, crop in aligned if label == direction_index],
                axis=0,
            ).astype(np.float32)
            for direction_index in range(len(DIRECTION_NAMES))
        }
    return templates


def _cross_validate(
    image_records: list[_ImageRecord],
    slot_records: list[_SlotRecord],
    folds: int,
) -> tuple[list[FoldReport], list[ImagePrediction], dict[str, dict[str, int]]]:
    folds = max(2, min(folds, len(image_records)))
    fold_reports: list[FoldReport] = []
    image_results: list[ImagePrediction] = []
    confusion = {
        actual: {predicted: 0 for predicted in DIRECTION_NAMES}
        for actual in DIRECTION_NAMES
    }

    for fold in range(folds):
        test_indices = {
            image_record.index
            for image_record in image_records
            if image_record.index % folds == fold
        }
        train_indices = {
            image_record.index
            for image_record in image_records
            if image_record.index not in test_indices
        }
        model = RuneTemplateModel.train(slot_records, train_indices)
        fold_results = [
            _predict_positive_image(record, model, threshold=0.0)
            for record in image_records
            if record.index in test_indices
        ]
        image_results.extend(fold_results)
        total_slots = 0
        correct_slots = 0
        for result in fold_results:
            for slot in result.slots:
                if slot.expected is not None:
                    total_slots += 1
                    correct_slots += int(bool(slot.correct))
                    confusion[slot.expected][slot.predicted] += 1
        fold_reports.append(
            FoldReport(
                fold=fold,
                image_count=len(fold_results),
                arrow_accuracy=_ratio(correct_slots, total_slots),
                sequence_accuracy=_ratio(
                    sum(1 for result in fold_results if result.sequence_correct),
                    len(fold_results),
                ),
            )
        )

    image_results.sort(key=lambda result: result.image)
    return fold_reports, image_results, confusion


def _predict_positive_image(
    record: _ImageRecord,
    model: RuneTemplateModel,
    threshold: float,
) -> ImagePrediction:
    reps_by_slot = {
        slot: {
            variant: _represent(_slot_crop(record.image, record.panel, slot, variant[0]))
            for variant in MODEL_VARIANTS
        }
        for slot in range(4)
    }
    return _predict_from_reps(
        image_path=record.path,
        expected=record.expected,
        expected_indices=record.expected_indices,
        panel=record.panel,
        reps_by_slot=reps_by_slot,
        model=model,
        threshold=threshold,
    )


def _predict_unlabeled_image(
    path: Path,
    model: RuneTemplateModel,
    threshold: float,
) -> ImagePrediction:
    image = _read_image(path)
    panel = locate_panel(image)
    reps_by_slot = {
        slot: {
            variant: _represent(_slot_crop(image, panel, slot, variant[0]))
            for variant in MODEL_VARIANTS
        }
        for slot in range(4)
    }
    return _predict_from_reps(
        image_path=path,
        expected=None,
        expected_indices=None,
        panel=panel,
        reps_by_slot=reps_by_slot,
        model=model,
        threshold=threshold,
    )


def _predict_from_reps(
    image_path: Path,
    expected: tuple[str, str, str, str] | None,
    expected_indices: tuple[int, int, int, int] | None,
    panel: PanelCandidate,
    reps_by_slot: dict[int, dict[tuple[int, int], np.ndarray]],
    model: RuneTemplateModel,
    threshold: float,
) -> ImagePrediction:
    slot_predictions: list[SlotPrediction] = []
    predicted: list[str] = []
    margins: list[float] = []
    top_scores: list[float] = []
    for slot in range(4):
        predicted_index, margin, top_score, scores, _ = model.predict_reps(reps_by_slot[slot])
        predicted_name = DIRECTION_NAMES[predicted_index]
        predicted.append(predicted_name)
        margins.append(margin)
        top_scores.append(top_score)
        expected_name = expected[slot] if expected is not None else None
        expected_index = expected_indices[slot] if expected_indices is not None else None
        slot_predictions.append(
            SlotPrediction(
                slot=slot,
                predicted=predicted_name,
                expected=expected_name,
                correct=(predicted_index == expected_index) if expected_index is not None else None,
                margin=margin,
                top_score=top_score,
                scores=scores,
            )
        )

    acceptance_score = _acceptance_score(panel.score, margins, top_scores)
    predicted_tuple = tuple(predicted)  # type: ignore[assignment]
    correct_count = None
    sequence_correct = None
    if expected is not None:
        correct_count = sum(
            1 for predicted_item, expected_item in zip(predicted_tuple, expected, strict=True)
            if predicted_item == expected_item
        )
        sequence_correct = predicted_tuple == expected
    return ImagePrediction(
        image=str(image_path),
        expected=expected,
        predicted=predicted_tuple,  # type: ignore[arg-type]
        accepted=acceptance_score >= threshold,
        panel=panel,
        acceptance_score=acceptance_score,
        margin_mean=float(np.mean(margins)),
        top_score_mean=float(np.mean(top_scores)),
        correct_count=correct_count,
        sequence_correct=sequence_correct,
        slots=tuple(slot_predictions),  # type: ignore[arg-type]
    )


def _calibrate_threshold(
    positive_results: list[ImagePrediction],
    negative_results: list[ImagePrediction],
) -> float:
    if not negative_results:
        return DEFAULT_UNKNOWN_THRESHOLD
    candidates = sorted(
        {result.acceptance_score for result in positive_results + negative_results}
    )
    if not candidates:
        return DEFAULT_UNKNOWN_THRESHOLD
    best_threshold = DEFAULT_UNKNOWN_THRESHOLD
    best_score = -1.0
    for left, right in zip(candidates, candidates[1:], strict=False):
        threshold = (left + right) / 2
        positive_accept_rate = _ratio(
            sum(1 for result in positive_results if result.acceptance_score >= threshold),
            len(positive_results),
        )
        negative_reject_rate = _ratio(
            sum(1 for result in negative_results if result.acceptance_score < threshold),
            len(negative_results),
        )
        score = positive_accept_rate + negative_reject_rate
        if score > best_score or (
            score == best_score and negative_reject_rate >= 0.99 and threshold > best_threshold
        ):
            best_score = score
            best_threshold = threshold
    return float(best_threshold)


def _replace_acceptance(result: ImagePrediction, accepted: bool) -> ImagePrediction:
    return ImagePrediction(
        image=result.image,
        expected=result.expected,
        predicted=result.predicted,
        accepted=accepted,
        panel=result.panel,
        acceptance_score=result.acceptance_score,
        margin_mean=result.margin_mean,
        top_score_mean=result.top_score_mean,
        correct_count=result.correct_count,
        sequence_correct=result.sequence_correct,
        slots=result.slots,
    )


def _acceptance_score(
    panel_score: float,
    margins: list[float],
    top_scores: list[float],
) -> float:
    return float(panel_score + 2.0 * np.mean(margins) + 0.5 * np.mean(top_scores))


def _slot_arrow_energy(
    hsv: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
) -> float:
    energy = 0.0
    for fraction in SLOT_FRACTIONS:
        center_x = int(x + width * fraction)
        crop = hsv[
            int(y + height * 0.25) : int(y + height * 0.75),
            max(0, center_x - 24) : min(hsv.shape[1], center_x + 24),
        ]
        mask = cv2.inRange(crop, np.array([0, 90, 120]), np.array([179, 255, 255]))
        energy += cv2.countNonZero(mask) / max(1, mask.size)
    return float(energy)


def _slot_crop(
    image: np.ndarray,
    panel: PanelCandidate,
    slot: int,
    size: int,
) -> np.ndarray:
    center_x = panel.x + panel.width * SLOT_FRACTIONS[slot]
    center_y = panel.y + panel.height * 0.52
    return _crop_centered(image, center_x, center_y, size)


def _crop_centered(
    image: np.ndarray,
    center_x: float,
    center_y: float,
    size: int,
) -> np.ndarray:
    x1 = int(round(center_x - size / 2))
    y1 = int(round(center_y - size / 2))
    output = np.zeros((size, size, 3), np.uint8)
    sx1 = max(0, x1)
    sy1 = max(0, y1)
    sx2 = min(image.shape[1], x1 + size)
    sy2 = min(image.shape[0], y1 + size)
    output[sy1 - y1 : sy2 - y1, sx1 - x1 : sx2 - x1] = image[sy1:sy2, sx1:sx2]
    return output


def _represent(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    chroma = (image.max(axis=2) - image.min(axis=2)).astype(np.float32) / 255
    blur = cv2.GaussianBlur(image, (31, 31), 0)
    diff = cv2.absdiff(image, blur).max(axis=2).astype(np.float32) / 255
    return np.dstack(
        [
            hsv[:, :, 1].astype(np.float32) / 255,
            hsv[:, :, 2].astype(np.float32) / 255,
            chroma,
            diff,
        ]
    ).astype(np.float32)


def _center_crop_repr(representation: np.ndarray, size: int) -> np.ndarray:
    height, width = representation.shape[:2]
    x = (width - size) // 2
    y = (height - size) // 2
    return representation[y : y + size, x : x + size]


_CAPSULE_CACHE: dict[tuple[int, int], np.ndarray] = {}


def _capsule_template(width: int, height: int) -> np.ndarray:
    key = (width, height)
    cached = _CAPSULE_CACHE.get(key)
    if cached is not None:
        return cached
    template = np.zeros((height, width), np.uint8)
    thickness = 3
    radius = height // 2 - thickness
    cv2.ellipse(
        template,
        (height // 2, height // 2),
        (height // 2 - thickness, height // 2 - thickness),
        90,
        0,
        180,
        255,
        thickness,
    )
    cv2.ellipse(
        template,
        (width - height // 2, height // 2),
        (height // 2 - thickness, height // 2 - thickness),
        270,
        0,
        180,
        255,
        thickness,
    )
    cv2.line(
        template,
        (height // 2, height // 2 - radius),
        (width - height // 2, height // 2 - radius),
        255,
        thickness,
    )
    cv2.line(
        template,
        (height // 2, height // 2 + radius),
        (width - height // 2, height // 2 + radius),
        255,
        thickness,
    )
    _CAPSULE_CACHE[key] = template
    return template


def _write_crops_and_contact_sheets(
    image_records: list[_ImageRecord],
    model: RuneTemplateModel,
    output_dir: Path,
) -> None:
    crops: list[tuple[np.ndarray, str, str, bool]] = []
    review_items: list[dict[str, int | str]] = []
    crops_dir = output_dir / "crops"
    for direction_name in DIRECTION_NAMES:
        (crops_dir / direction_name).mkdir(parents=True, exist_ok=True)

    first_variant = MODEL_VARIANTS[0]
    first_templates = model.templates[first_variant]
    for record in image_records:
        for slot, expected_index in enumerate(record.expected_indices):
            big_crop = _slot_crop(record.image, record.panel, slot, first_variant[0])
            representation = _represent(big_crop)
            template = first_templates[expected_index]
            result = cv2.matchTemplate(representation, template, cv2.TM_CCOEFF_NORMED)
            _, _, _, location = cv2.minMaxLoc(result)
            center_x = location[0] + first_variant[1] / 2
            center_y = location[1] + first_variant[1] / 2
            crop = _crop_centered(big_crop, center_x, center_y, 72)
            direction_name = DIRECTION_NAMES[expected_index]
            crop_name = f"{record.path.stem}_slot{slot + 1}_{direction_name}.png"
            crop_path = crops_dir / direction_name / crop_name
            _write_image(crop_path, crop)
            crops.append((crop, record.path.name, direction_name, True))
            review_items.append(
                {
                    "id": f"{record.path.stem}_slot{slot + 1}",
                    "source_image": record.path.name,
                    "slot": slot + 1,
                    "expected": direction_name,
                    "crop_path": _relative_posix(crop_path, output_dir / "review"),
                }
            )

    _write_contact_sheet(
        crops,
        output_dir / "review" / "crops_contact_sheet.png",
        columns=20,
        cell_size=88,
    )
    _write_crop_review_manifest(review_items, output_dir / "review")
    _write_crop_review_html(review_items, output_dir / "review" / "crop_review.html")


def _write_debug_images(
    positive_results: list[ImagePrediction],
    negative_results: list[ImagePrediction],
    output_dir: Path,
) -> None:
    error_tiles: list[tuple[np.ndarray, str, str, bool]] = []
    for result in positive_results:
        image = _read_image(Path(result.image))
        debug = _draw_debug_image(image, result)
        debug_path = output_dir / "debug" / "positive" / f"{Path(result.image).stem}_debug.png"
        _write_image(debug_path, debug)
        if result.sequence_correct is False:
            tile = cv2.resize(debug, (220, 124), interpolation=cv2.INTER_AREA)
            expected = "".join(DIRECTION_KEYS[item] for item in result.expected or ())
            predicted = "".join(DIRECTION_KEYS[item] for item in result.predicted)
            error_tiles.append((tile, Path(result.image).name, f"{expected}>{predicted}", False))

    for result in negative_results:
        image = _read_image(Path(result.image))
        debug = _draw_debug_image(image, result)
        debug_path = output_dir / "debug" / "negative" / f"{Path(result.image).stem}_debug.png"
        _write_image(debug_path, debug)

    if error_tiles:
        _write_contact_sheet(
            error_tiles,
            output_dir / "review" / "errors_contact_sheet.png",
            columns=4,
            cell_size=240,
        )


def _draw_debug_image(image: np.ndarray, result: ImagePrediction) -> np.ndarray:
    output = image.copy()
    panel = result.panel
    color = (0, 220, 0) if result.accepted else (0, 180, 255)
    cv2.rectangle(
        output,
        (panel.x, panel.y),
        (panel.x + panel.width, panel.y + panel.height),
        color,
        2,
    )
    for slot, prediction in enumerate(result.slots):
        center_x = int(panel.x + panel.width * SLOT_FRACTIONS[slot])
        center_y = int(panel.y + panel.height * 0.52)
        cv2.rectangle(
            output,
            (center_x - 66, center_y - 66),
            (center_x + 66, center_y + 66),
            color,
            1,
        )
        label = (
            f"{slot + 1}:{DIRECTION_KEYS[prediction.predicted]} "
            f"{prediction.margin:.2f}"
        )
        cv2.putText(
            output,
            label,
            (center_x - 44, center_y - 72),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
    header = (
        f"accepted={result.accepted} score={result.acceptance_score:.2f} "
        f"panel={panel.score:.2f} margin={result.margin_mean:.2f}"
    )
    cv2.putText(
        output,
        header,
        (20, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return output


def _write_contact_sheet(
    tiles: list[tuple[np.ndarray, str, str, bool]],
    output_path: Path,
    columns: int,
    cell_size: int,
) -> None:
    if not tiles:
        return
    rows = int(np.ceil(len(tiles) / columns))
    sheet = np.zeros((rows * cell_size, columns * cell_size, 3), np.uint8)
    for index, (tile, _source, label, ok) in enumerate(tiles):
        row = index // columns
        column = index % columns
        resized = cv2.resize(tile, (cell_size, cell_size), interpolation=cv2.INTER_AREA)
        x = column * cell_size
        y = row * cell_size
        sheet[y : y + cell_size, x : x + cell_size] = resized
        color = (0, 220, 0) if ok else (0, 0, 255)
        cv2.rectangle(sheet, (x, y), (x + cell_size - 1, y + cell_size - 1), color, 1)
        cv2.putText(
            sheet,
            label,
            (x + 3, y + 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
    _write_image(output_path, sheet)


def _write_crop_review_manifest(
    items: list[dict[str, int | str]],
    review_dir: Path,
) -> None:
    path = review_dir / "crop_review_manifest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "id",
                "source_image",
                "slot",
                "expected",
                "corrected_expected",
                "crop_path",
                "review",
                "notes",
            ]
        )
        for item in items:
            writer.writerow(
                [
                    item["id"],
                    item["source_image"],
                    item["slot"],
                    item["expected"],
                    "",
                    item["crop_path"],
                    "",
                    "",
                ]
            )


def _write_crop_review_html(
    items: list[dict[str, int | str]],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items_json = json.dumps(items, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rune Crop Review</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: "Segoe UI", Arial, sans-serif;
      background: #101418;
      color: #e8eef4;
    }}
    body {{
      margin: 0;
      background: #101418;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      padding: 12px 16px;
      border-bottom: 1px solid #2e3843;
      background: rgba(16, 20, 24, 0.96);
    }}
    .title {{
      margin-right: 12px;
      font-weight: 700;
    }}
    button, select, input {{
      border: 1px solid #3a4652;
      border-radius: 6px;
      background: #1b232b;
      color: #e8eef4;
      padding: 7px 10px;
      font: inherit;
    }}
    button {{
      cursor: pointer;
    }}
    button:hover {{
      background: #26313b;
    }}
    .stats {{
      color: #b8c7d6;
      font-size: 13px;
    }}
    main {{
      padding: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(138px, 1fr));
      gap: 10px;
    }}
    .card {{
      border: 1px solid #2f3a45;
      border-radius: 8px;
      background: #171d23;
      overflow: hidden;
    }}
    .card:focus {{
      outline: 2px solid #8dd3ff;
      outline-offset: 2px;
    }}
    .card.ok {{
      border-color: #33c56c;
    }}
    .card.bad {{
      border-color: #ff5f57;
    }}
    .card.hard {{
      border-color: #f7c948;
    }}
    .image-wrap {{
      display: grid;
      place-items: center;
      height: 112px;
      background: #0b0f13;
    }}
    img {{
      width: 96px;
      height: 96px;
      image-rendering: pixelated;
      object-fit: contain;
    }}
    .meta {{
      padding: 8px;
      font-size: 12px;
      color: #b8c7d6;
      overflow-wrap: anywhere;
    }}
    .expected {{
      color: #ffffff;
      font-weight: 700;
    }}
    .card.corrected {{
      box-shadow: inset 0 0 0 2px #8dd3ff;
    }}
    .label-row {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 4px;
      padding: 0 8px 8px;
      font-size: 12px;
      color: #b8c7d6;
    }}
    .label-row select {{
      width: 100%;
      padding: 5px 6px;
      font-size: 12px;
    }}
    .corrected-text {{
      color: #8dd3ff;
      font-weight: 700;
    }}
    .choices {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 4px;
      padding: 0 8px 8px;
    }}
    .choices button {{
      padding: 6px 3px;
      font-size: 12px;
    }}
    .choices button.active {{
      color: #0b0f13;
      font-weight: 700;
    }}
    .choices button[data-review="ok"].active {{
      background: #33c56c;
    }}
    .choices button[data-review="bad"].active {{
      background: #ff5f57;
    }}
    .choices button[data-review="hard"].active {{
      background: #f7c948;
    }}
    .notes {{
      width: calc(100% - 16px);
      margin: 0 8px 8px;
      box-sizing: border-box;
      font-size: 12px;
    }}
    .hidden {{
      display: none;
    }}
  </style>
</head>
<body>
  <header>
    <div class="title">Rune Crop Review</div>
    <select id="statusFilter">
      <option value="all">全部</option>
      <option value="pending">未审</option>
      <option value="ok">OK</option>
      <option value="bad">Bad</option>
      <option value="hard">Hard</option>
    </select>
    <select id="directionFilter">
      <option value="all">全部方向</option>
      <option value="up">up</option>
      <option value="down">down</option>
      <option value="left">left</option>
      <option value="right">right</option>
    </select>
    <input id="searchBox" type="search" placeholder="搜索 type/slot">
    <button id="markVisibleOk" type="button">可见项标 OK</button>
    <button id="clearVisible" type="button">清空可见项</button>
    <button id="exportCsv" type="button">导出 CSV</button>
    <span class="stats" id="stats"></span>
  </header>
  <main>
    <div class="grid" id="grid"></div>
  </main>
  <script>
    const ITEMS = {items_json};
    const STORAGE_KEY = "mxds-rune-crop-review-v1";
    const state = loadState();
    const grid = document.getElementById("grid");
    const statusFilter = document.getElementById("statusFilter");
    const directionFilter = document.getElementById("directionFilter");
    const searchBox = document.getElementById("searchBox");
    const stats = document.getElementById("stats");

    function loadState() {{
      try {{
        return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");
      }} catch (_error) {{
        return {{}};
      }}
    }}

    function saveState() {{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    }}

    function reviewOf(item) {{
      return state[item.id]?.review || "pending";
    }}

    function notesOf(item) {{
      return state[item.id]?.notes || "";
    }}

    function correctedExpectedOf(item) {{
      return state[item.id]?.corrected_expected || item.expected;
    }}

    function setReview(item, review) {{
      state[item.id] = {{ ...(state[item.id] || {{}}), review }};
      if (review === "pending") {{
        delete state[item.id].review;
      }}
      saveState();
      render();
    }}

    function setNotes(item, notes) {{
      state[item.id] = {{ ...(state[item.id] || {{}}), notes }};
      if (!notes) {{
        delete state[item.id].notes;
      }}
      saveState();
    }}

    function setCorrectedExpected(item, correctedExpected) {{
      state[item.id] = {{
        ...(state[item.id] || {{}}),
        corrected_expected: correctedExpected,
      }};
      if (correctedExpected === item.expected) {{
        delete state[item.id].corrected_expected;
      }}
      saveState();
      render();
    }}

    function itemVisible(item) {{
      const status = reviewOf(item);
      const wantedStatus = statusFilter.value;
      const wantedDirection = directionFilter.value;
      const query = searchBox.value.trim().toLowerCase();
      if (wantedStatus !== "all" && status !== wantedStatus) return false;
      if (wantedDirection !== "all" && correctedExpectedOf(item) !== wantedDirection) {{
        return false;
      }}
      if (query && !`${{item.id}} ${{item.source_image}}`.toLowerCase().includes(query)) {{
        return false;
      }}
      return true;
    }}

    function render() {{
      grid.textContent = "";
      let visible = 0;
      let corrected = 0;
      const counts = {{ pending: 0, ok: 0, bad: 0, hard: 0 }};
      for (const item of ITEMS) {{
        counts[reviewOf(item)] += 1;
        if (correctedExpectedOf(item) !== item.expected) corrected += 1;
        if (!itemVisible(item)) continue;
        visible += 1;
        grid.appendChild(cardFor(item));
      }}
      stats.textContent =
        `显示 ${{visible}} / ${{ITEMS.length}} | ` +
        `未审 ${{counts.pending}} · OK ${{counts.ok}} · ` +
        `Bad ${{counts.bad}} · Hard ${{counts.hard}} · 修正 ${{corrected}}`;
    }}

    function cardFor(item) {{
      const review = reviewOf(item);
      const correctedExpected = correctedExpectedOf(item);
      const card = document.createElement("section");
      card.className =
        `card ${{review === "pending" ? "" : review}} ` +
        `${{correctedExpected !== item.expected ? "corrected" : ""}}`;
      card.tabIndex = 0;
      card.dataset.id = item.id;

      const imageWrap = document.createElement("div");
      imageWrap.className = "image-wrap";
      const image = document.createElement("img");
      image.src = item.crop_path;
      image.alt = item.id;
      imageWrap.appendChild(image);

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.innerHTML =
        `<div class="expected">${{item.expected}} · slot ${{item.slot}}</div>` +
        `<div>修正: <span class="corrected-text">${{correctedExpected}}</span></div>` +
        `<div>${{item.source_image}}</div>`;

      const choices = document.createElement("div");
      choices.className = "choices";
      for (const value of ["ok", "bad", "hard"]) {{
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.review = value;
        button.textContent = value.toUpperCase();
        button.className = review === value ? "active" : "";
        button.addEventListener("click", () => setReview(item, value));
        choices.appendChild(button);
      }}

      const labelRow = document.createElement("label");
      labelRow.className = "label-row";
      labelRow.textContent = "修正方向";
      const labelSelect = document.createElement("select");
      for (const value of ["up", "down", "left", "right"]) {{
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        option.selected = value === correctedExpected;
        labelSelect.appendChild(option);
      }}
      labelSelect.addEventListener("change", () => {{
        setCorrectedExpected(item, labelSelect.value);
      }});
      labelRow.appendChild(labelSelect);

      const notes = document.createElement("input");
      notes.className = "notes";
      notes.placeholder = "备注";
      notes.value = notesOf(item);
      notes.addEventListener("change", () => setNotes(item, notes.value.trim()));

      card.append(imageWrap, meta, choices, labelRow, notes);
      return card;
    }}

    function visibleItems() {{
      return ITEMS.filter(itemVisible);
    }}

    function exportCsv() {{
      const rows = [[
        "id",
        "source_image",
        "slot",
        "expected",
        "corrected_expected",
        "crop_path",
        "review",
        "notes",
      ]];
      for (const item of ITEMS) {{
        rows.push([
          item.id,
          item.source_image,
          item.slot,
          item.expected,
          correctedExpectedOf(item) === item.expected ? "" : correctedExpectedOf(item),
          item.crop_path,
          reviewOf(item) === "pending" ? "" : reviewOf(item),
          notesOf(item),
        ]);
      }}
      const csv = rows.map(row => row.map(csvCell).join(",")).join("\\n");
      const blob = new Blob(["\\ufeff" + csv], {{ type: "text/csv;charset=utf-8" }});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "crop_review_results.csv";
      link.click();
      URL.revokeObjectURL(link.href);
    }}

    function csvCell(value) {{
      const text = String(value ?? "");
      return `"${{text.replaceAll('"', '""')}}"`;
    }}

    function focusMove(delta) {{
      const cards = Array.from(document.querySelectorAll(".card"));
      const index = cards.indexOf(document.activeElement);
      const next = cards[Math.max(0, Math.min(cards.length - 1, index + delta))];
      if (next) next.focus();
    }}

    statusFilter.addEventListener("change", render);
    directionFilter.addEventListener("change", render);
    searchBox.addEventListener("input", render);
    document.getElementById("exportCsv").addEventListener("click", exportCsv);
    document.getElementById("markVisibleOk").addEventListener("click", () => {{
      for (const item of visibleItems()) setReview(item, "ok");
    }});
    document.getElementById("clearVisible").addEventListener("click", () => {{
      for (const item of visibleItems()) setReview(item, "pending");
    }});
    document.addEventListener("keydown", event => {{
      const card = document.activeElement?.closest?.(".card");
      if (!card) return;
      const item = ITEMS.find(candidate => candidate.id === card.dataset.id);
      if (!item) return;
      if (event.key === "1") setReview(item, "ok");
      if (event.key === "2") setReview(item, "bad");
      if (event.key === "3") setReview(item, "hard");
      if (event.key === "ArrowRight") focusMove(1);
      if (event.key === "ArrowLeft") focusMove(-1);
    }});
    render();
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def _write_report_files(report: TrainingReport, output_dir: Path) -> None:
    serializable = _to_jsonable(asdict(report))
    (output_dir / "summary.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_predictions_csv(output_dir / "predictions.csv", report.positive_results)
    _write_predictions_csv(output_dir / "negative_predictions.csv", report.negative_results)
    _write_confusion_csv(output_dir / "confusion_matrix.csv", report.confusion_matrix)


def _write_predictions_csv(path: Path, results: list[ImagePrediction]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "image",
                "expected",
                "predicted",
                "accepted",
                "acceptance_score",
                "panel_score",
                "margin_mean",
                "top_score_mean",
                "correct_count",
                "sequence_correct",
            ]
        )
        for result in results:
            writer.writerow(
                [
                    result.image,
                    "".join(DIRECTION_KEYS[item] for item in result.expected or ()),
                    "".join(DIRECTION_KEYS[item] for item in result.predicted),
                    result.accepted,
                    f"{result.acceptance_score:.6f}",
                    f"{result.panel.score:.6f}",
                    f"{result.margin_mean:.6f}",
                    f"{result.top_score_mean:.6f}",
                    result.correct_count,
                    result.sequence_correct,
                ]
            )


def _write_confusion_csv(path: Path, confusion: dict[str, dict[str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["actual", *DIRECTION_NAMES])
        for actual in DIRECTION_NAMES:
            writer.writerow(
                [actual, *[confusion[actual][predicted] for predicted in DIRECTION_NAMES]]
            )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _iter_image_paths(directory: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        ],
        key=lambda path: path.name,
    )


def _relative_posix(path: Path, start: Path) -> str:
    return Path(os.path.relpath(path.resolve(), start.resolve())).as_posix()


def _read_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = cv2.imencode(".png", image)[1]
    path.write_bytes(encoded.tobytes())


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate offline rune recognition.")
    parser.add_argument(
        "--positive",
        type=Path,
        required=True,
        help="RuneInstance image directory.",
    )
    parser.add_argument(
        "--negative",
        type=Path,
        default=None,
        help="NoRuneUI negative image directory.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    parser.add_argument("--folds", type=int, default=5, help="Image-level cross validation folds.")
    args = parser.parse_args()

    report = run_training(args.positive, args.output, args.negative, args.folds)
    print(f"positive_count={report.positive_count}")
    print(f"negative_count={report.negative_count}")
    print(f"crop_count={report.crop_count}")
    print(f"cross_validation_arrow_accuracy={report.cross_validation_arrow_accuracy:.4f}")
    print(f"cross_validation_sequence_accuracy={report.cross_validation_sequence_accuracy:.4f}")
    print(f"final_positive_sequence_accuracy={report.final_positive_sequence_accuracy:.4f}")
    print(f"final_positive_accept_rate={report.final_positive_accept_rate:.4f}")
    if report.no_rune_ui_reject_rate is not None:
        print(f"no_rune_ui_reject_rate={report.no_rune_ui_reject_rate:.4f}")
    print(f"unknown_threshold={report.unknown_threshold:.4f}")
    print(f"summary={args.output / 'summary.json'}")


if __name__ == "__main__":
    main()
