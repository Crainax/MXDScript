from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

from mhscript_yjs.core.config import project_root
from mhscript_yjs.scripts.tool.rune_train import (
    DIRECTION_NAMES,
    SLOT_FRACTIONS,
    PanelCandidate,
    RuneTemplateModel,
    TemplateMatchLocation,
    _acceptance_score,
    _represent,
    _slot_crop,
    locate_panel,
    locate_panel_expanded,
)

DIRECTION_LABELS = {
    "\u4e0a": "up",
    "\u4e0b": "down",
    "\u5de6": "left",
    "\u53f3": "right",
}
DIRECTION_KEYS = {
    "up": "U",
    "down": "D",
    "left": "L",
    "right": "R",
    "unknown": "?",
}
SLOT_OFFSETS = (-138, -46, 46, 138)
DEFAULT_RUNE_MODEL_PATH = Path("assets") / "Rune" / "rune_template_model.npz"
LEGACY_RUNE_MODEL_PATH = (
    Path("protype") / "RuneTrainPhase5Output" / "model" / "rune_template_model.npz"
)
MODEL_TOP_SCORE_THRESHOLD = 0.55


@dataclass(frozen=True)
class SlotPrediction:
    slot: int
    slot_x: float
    predicted: str
    confidence: float
    raw_score: float
    match_x: int
    match_y: int
    all_scores: dict[str, float]


@dataclass(frozen=True)
class RunePrediction:
    predicted: tuple[str, str, str, str]
    group_center_x: float
    score: float
    slots: tuple[SlotPrediction, SlotPrediction, SlotPrediction, SlotPrediction]
    selection_score: float | None = None


@dataclass(frozen=True)
class RuneImageResult:
    image: str
    expected: tuple[str, ...] | None
    predicted: tuple[str, str, str, str]
    correct_count: int | None
    sequence_correct: bool | None
    group_center_x: float
    score: float
    debug_image: str
    slots: tuple[SlotPrediction, SlotPrediction, SlotPrediction, SlotPrediction]


@dataclass(frozen=True)
class RuneDirectoryReport:
    input_dir: str
    output_dir: str
    image_count: int
    labeled_count: int
    arrow_accuracy: float | None
    sequence_accuracy: float | None
    traditional_cv_sufficient: bool
    conclusion: str
    results: list[RuneImageResult]


class RuneCvRecognizer:
    def __init__(self) -> None:
        self._templates = {
            direction: _make_arrow_template(direction)
            for direction in ("up", "down", "left", "right")
        }

    def recognize(self, image: np.ndarray) -> RunePrediction:
        best: RunePrediction | None = None
        for center_x in np.arange(650.0, 755.1, 1.0):
            slots = tuple(
                self._predict_slot(image, index, center_x + offset)
                for index, offset in enumerate(SLOT_OFFSETS)
            )
            score = sum(slot.confidence for slot in slots)
            score -= 0.0008 * abs(center_x - 690.0)
            prediction = RunePrediction(
                predicted=tuple(slot.predicted for slot in slots),  # type: ignore[arg-type]
                group_center_x=float(center_x),
                score=float(score),
                slots=slots,  # type: ignore[arg-type]
            )
            if best is None or prediction.score > best.score:
                best = prediction
        if best is None:
            raise RuntimeError("Rune recognizer did not produce a candidate.")
        return best

    def _predict_slot(self, image: np.ndarray, slot: int, slot_x: float) -> SlotPrediction:
        scores: dict[str, tuple[float, float, int, int]] = {}
        for direction, template in self._templates.items():
            raw_score, match_x, match_y = _best_template_score_in_slot(image, template, slot_x)
            adjusted = raw_score
            adjusted -= 0.0035 * abs(match_x - slot_x)
            adjusted -= 0.0025 * max(0.0, abs(match_y - 240.0) - 30.0)
            scores[direction] = (float(adjusted), float(raw_score), int(match_x), int(match_y))

        predicted, best = max(scores.items(), key=lambda item: item[1][0])
        adjusted_score, raw_score, match_x, match_y = best
        return SlotPrediction(
            slot=slot,
            slot_x=float(slot_x),
            predicted=predicted,
            confidence=float(adjusted_score),
            raw_score=float(raw_score),
            match_x=match_x,
            match_y=match_y,
            all_scores={direction: value[0] for direction, value in scores.items()},
        )


class RuneModelRecognizer:
    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path.resolve()
        self._model, self._threshold = RuneTemplateModel.load(self.model_path)

    @property
    def threshold(self) -> float:
        return self._threshold

    def recognize(self, image: np.ndarray) -> RunePrediction:
        legacy_panel = locate_panel(image)
        legacy_prediction = self._recognize_with_panel(image, legacy_panel)
        expanded_panel = locate_panel_expanded(image)
        if _same_panel(legacy_panel, expanded_panel):
            return legacy_prediction

        expanded_prediction = self._recognize_with_panel(image, expanded_panel)
        if _should_use_expanded_panel(
            legacy_panel,
            legacy_prediction,
            expanded_panel,
            expanded_prediction,
        ):
            return expanded_prediction
        return legacy_prediction

    def _recognize_with_panel(self, image: np.ndarray, panel: PanelCandidate) -> RunePrediction:
        slots: list[SlotPrediction] = []
        predicted: list[str] = []
        margins: list[float] = []
        top_scores: list[float] = []
        for slot in range(4):
            reps = {
                variant: _represent(_slot_crop(image, panel, slot, variant[0]))
                for variant in self._model.templates
            }
            predicted_index, margin, top_score, scores, location = self._model.predict_reps(reps)
            match_x, match_y = _match_location_in_image(panel, slot, location)
            predicted_name = DIRECTION_NAMES[predicted_index]
            predicted.append(predicted_name)
            margins.append(margin)
            top_scores.append(top_score)
            slots.append(
                SlotPrediction(
                    slot=slot,
                    slot_x=float(match_x),
                    predicted=predicted_name,
                    confidence=top_score,
                    raw_score=top_score,
                    match_x=match_x,
                    match_y=match_y,
                    all_scores=scores,
                )
            )

        acceptance_score = _acceptance_score(panel.score, margins, top_scores)
        normalized_score = min(
            acceptance_score / max(self._threshold, 1e-6),
            float(np.mean(top_scores)) / MODEL_TOP_SCORE_THRESHOLD,
        )
        return RunePrediction(
            predicted=tuple(predicted),  # type: ignore[arg-type]
            group_center_x=float(np.mean([slot.slot_x for slot in slots])),
            score=float(normalized_score),
            slots=tuple(slots),  # type: ignore[arg-type]
            selection_score=float(acceptance_score / max(self._threshold, 1e-6)),
        )


def _same_panel(first: PanelCandidate, second: PanelCandidate) -> bool:
    return (
        first.x == second.x
        and first.y == second.y
        and first.width == second.width
        and first.height == second.height
    )


def _should_use_expanded_panel(
    legacy_panel: PanelCandidate,
    legacy_prediction: RunePrediction,
    expanded_panel: PanelCandidate,
    expanded_prediction: RunePrediction,
) -> bool:
    legacy_score = legacy_prediction.selection_score or legacy_prediction.score
    expanded_score = expanded_prediction.selection_score or expanded_prediction.score
    score_gain = expanded_score - legacy_score
    if expanded_panel.y > 185:
        return False
    if score_gain < 0.25:
        return False
    return legacy_panel.score < 0.58 or score_gain >= 0.55


def default_rune_model_path() -> Path:
    preferred = (project_root() / DEFAULT_RUNE_MODEL_PATH).resolve()
    if preferred.exists():
        return preferred
    legacy = (project_root() / LEGACY_RUNE_MODEL_PATH).resolve()
    if legacy.exists():
        return legacy
    return preferred


def load_rune_recognizer(model_path: Path | None = None) -> RuneModelRecognizer | RuneCvRecognizer:
    path = model_path.resolve() if model_path is not None else default_rune_model_path()
    if path.exists():
        return RuneModelRecognizer(path)
    return RuneCvRecognizer()


def _match_location_in_image(
    panel: PanelCandidate,
    slot: int,
    location: TemplateMatchLocation,
) -> tuple[int, int]:
    fixed_x = panel.x + panel.width * SLOT_FRACTIONS[slot]
    fixed_y = panel.y + panel.height * 0.52
    left = fixed_x - location.big_size / 2
    top = fixed_y - location.big_size / 2
    return (
        int(round(left + location.x + location.small_size / 2)),
        int(round(top + location.y + location.small_size / 2)),
    )


def analyze_directory(
    input_dir: Path,
    output_dir: Path,
    model_path: Path | None = None,
) -> RuneDirectoryReport:
    recognizer = (
        RuneModelRecognizer(model_path.resolve())
        if model_path is not None
        else RuneCvRecognizer()
    )
    recognizer_name = (
        "template_model" if isinstance(recognizer, RuneModelRecognizer) else "traditional_cv"
    )
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    debug_dir = output_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(
        [
            path
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}
        ],
        key=lambda path: path.name,
    )
    results: list[RuneImageResult] = []
    total_correct = 0
    total_arrows = 0
    sequence_correct = 0
    labeled_count = 0

    for image_path in image_paths:
        image = _read_image(image_path)
        expected = parse_expected_sequence(image_path)
        prediction = recognizer.recognize(image)
        debug_path = debug_dir / f"{image_path.stem}_debug.png"
        _write_debug_image(image, expected, prediction, debug_path)

        correct_count: int | None = None
        is_sequence_correct: bool | None = None
        if expected is not None:
            labeled_count += 1
            correct_count = sum(
                1
                for predicted, actual in zip(prediction.predicted, expected, strict=True)
                if predicted == actual
            )
            is_sequence_correct = prediction.predicted == expected
            total_correct += correct_count
            total_arrows += len(expected)
            sequence_correct += int(is_sequence_correct)

        results.append(
            RuneImageResult(
                image=str(image_path),
                expected=expected,
                predicted=prediction.predicted,
                correct_count=correct_count,
                sequence_correct=is_sequence_correct,
                group_center_x=prediction.group_center_x,
                score=prediction.score,
                debug_image=str(debug_path),
                slots=prediction.slots,
            )
        )

    arrow_accuracy = (total_correct / total_arrows) if total_arrows else None
    sequence_accuracy = (sequence_correct / labeled_count) if labeled_count else None
    sufficient = bool(
        arrow_accuracy is not None
        and arrow_accuracy >= 0.95
        and sequence_accuracy is not None
        and sequence_accuracy >= 0.9
    )
    conclusion = (
        f"{recognizer_name}_sufficient"
        if sufficient
        else f"{recognizer_name}_not_sufficient_for_reliable_release"
    )
    report = RuneDirectoryReport(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        image_count=len(image_paths),
        labeled_count=labeled_count,
        arrow_accuracy=arrow_accuracy,
        sequence_accuracy=sequence_accuracy,
        traditional_cv_sufficient=sufficient,
        conclusion=conclusion,
        results=results,
    )
    _write_report_files(report, output_dir)
    return report


def parse_expected_sequence(path: Path) -> tuple[str, str, str, str] | None:
    if "_" not in path.stem:
        return None
    suffix = path.stem.rsplit("_", 1)[1]
    if len(suffix) != 4:
        return None
    directions: list[str] = []
    for char in suffix:
        direction = DIRECTION_LABELS.get(char)
        if direction is None:
            return None
        directions.append(direction)
    return tuple(directions)  # type: ignore[return-value]


def _make_arrow_template(direction: str, size: int = 31) -> np.ndarray:
    mask = np.zeros((size, size), np.uint8)
    center = size // 2
    if direction == "right":
        points = np.array(
            [
                [4, center - 6],
                [16, center - 6],
                [16, center - 11],
                [28, center],
                [16, center + 11],
                [16, center + 6],
                [4, center + 6],
            ],
            np.int32,
        )
    elif direction == "left":
        points = np.array(
            [
                [27, center - 6],
                [15, center - 6],
                [15, center - 11],
                [3, center],
                [15, center + 11],
                [15, center + 6],
                [27, center + 6],
            ],
            np.int32,
        )
    elif direction == "up":
        points = np.array(
            [
                [center - 6, 27],
                [center - 6, 15],
                [center - 11, 15],
                [center, 3],
                [center + 11, 15],
                [center + 6, 15],
                [center + 6, 27],
            ],
            np.int32,
        )
    elif direction == "down":
        points = np.array(
            [
                [center - 6, 4],
                [center - 6, 16],
                [center - 11, 16],
                [center, 28],
                [center + 11, 16],
                [center + 6, 16],
                [center + 6, 4],
            ],
            np.int32,
        )
    else:
        raise ValueError(f"Unknown direction: {direction}")
    cv2.fillPoly(mask, [points], 255)
    return mask.astype(np.float32) / 255.0


def _best_template_score_in_slot(
    image: np.ndarray,
    template: np.ndarray,
    slot_x: float,
    *,
    expected_y: int = 240,
    radius_x: int = 36,
    radius_y: int = 46,
) -> tuple[float, int, int]:
    x0 = max(0, int(round(slot_x - radius_x)))
    x1 = min(image.shape[1], int(round(slot_x + radius_x)))
    y0 = max(0, expected_y - radius_y)
    y1 = min(image.shape[0], expected_y + radius_y)
    mask = _arrow_color_mask(image[y0:y1, x0:x1])
    if mask.shape[0] < template.shape[0] or mask.shape[1] < template.shape[1]:
        return 0.0, int(slot_x), expected_y
    result = cv2.matchTemplate(mask, template, cv2.TM_CCOEFF_NORMED)
    _, max_value, _, max_location = cv2.minMaxLoc(result)
    match_x = x0 + max_location[0] + template.shape[1] // 2
    match_y = y0 + max_location[1] + template.shape[0] // 2
    return float(max_value), int(match_x), int(match_y)


def _arrow_color_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 1] > 75) & (hsv[:, :, 2] > 125)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8), iterations=1)
    return mask.astype(np.float32) / 255.0


def _read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Could not read image: {path}")
    return image


def _write_debug_image(
    image: np.ndarray,
    expected: tuple[str, ...] | None,
    prediction: RunePrediction,
    output_path: Path,
) -> None:
    output = image.copy()
    cv2.rectangle(output, (430, 194), (980, 286), (80, 80, 80), 1)
    for slot in prediction.slots:
        color = (0, 220, 255)
        top_left = (int(round(slot.slot_x - 36)), 194)
        bottom_right = (int(round(slot.slot_x + 36)), 286)
        cv2.rectangle(output, top_left, bottom_right, color, 2)
        cv2.circle(output, (slot.match_x, slot.match_y), 6, (0, 0, 255), 2)
        label = f"{slot.slot + 1}:{DIRECTION_KEYS[slot.predicted]} {slot.confidence:.2f}"
        cv2.putText(
            output,
            label,
            (top_left[0], max(18, top_left[1] - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            color,
            1,
            cv2.LINE_AA,
        )
    expected_text = "-" if expected is None else "".join(DIRECTION_KEYS[item] for item in expected)
    predicted_text = "".join(DIRECTION_KEYS[item] for item in prediction.predicted)
    header = (
        f"expected={expected_text} predicted={predicted_text} "
        f"center={prediction.group_center_x:.0f} score={prediction.score:.2f}"
    )
    cv2.putText(
        output,
        header,
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = cv2.imencode(".png", output)[1]
    output_path.write_bytes(encoded.tobytes())


def _write_report_files(report: RuneDirectoryReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as stream:
        json.dump(asdict(report), stream, ensure_ascii=False, indent=2)

    csv_path = output_dir / "summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "image",
                "expected",
                "predicted",
                "correct_count",
                "sequence_correct",
                "group_center_x",
                "score",
                "debug_image",
            ],
        )
        writer.writeheader()
        for result in report.results:
            writer.writerow(
                {
                    "image": result.image,
                    "expected": _sequence_to_key_string(result.expected),
                    "predicted": _sequence_to_key_string(result.predicted),
                    "correct_count": result.correct_count,
                    "sequence_correct": result.sequence_correct,
                    "group_center_x": f"{result.group_center_x:.1f}",
                    "score": f"{result.score:.4f}",
                    "debug_image": result.debug_image,
                }
            )


def _sequence_to_key_string(sequence: tuple[str, ...] | None) -> str:
    if sequence is None:
        return ""
    return "".join(DIRECTION_KEYS.get(item, "?") for item in sequence)


def _print_report(report: RuneDirectoryReport) -> None:
    arrow_text = "-" if report.arrow_accuracy is None else f"{report.arrow_accuracy:.2%}"
    sequence_text = "-" if report.sequence_accuracy is None else f"{report.sequence_accuracy:.2%}"
    print(f"images={report.image_count} labeled={report.labeled_count}")
    print(f"arrow_accuracy={arrow_text} sequence_accuracy={sequence_text}")
    print(f"conclusion={report.conclusion}")
    print(f"summary={Path(report.output_dir) / 'summary.json'}")
    print(f"debug_dir={Path(report.output_dir) / 'debug'}")
    for result in report.results:
        expected = _sequence_to_key_string(result.expected) or "-"
        predicted = _sequence_to_key_string(result.predicted)
        print(
            f"{Path(result.image).name}: expected={expected} predicted={predicted} "
            f"correct={result.correct_count} center={result.group_center_x:.0f} "
            f"debug={result.debug_image}"
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline rune direction recognition experiment.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("protype") / "RuneInstance",
        help="Directory containing full-screen rune screenshots.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("protype") / "RuneDebugOutput",
        help="Directory for summary files and debug overlays.",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=None,
        help="Optional trained rune_template_model.npz to use instead of legacy CV.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    report = analyze_directory(args.input, args.output, args.model)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
