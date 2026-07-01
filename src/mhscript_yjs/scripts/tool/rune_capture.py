from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from mhscript_yjs.runtime.control import StopRequested
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.runtime.timing import Sleeper
from mhscript_yjs.scripts.tool.rune_debug import DIRECTION_KEYS, RuneCvRecognizer, RunePrediction
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.vision.types import Region
from mhscript_yjs.windows.maple import WindowInfo, refresh_window_info

if TYPE_CHECKING:
    from mhscript_yjs.scripts.registry import ScriptRunContext


RUNE_CAPTURE_SCRIPT_ID = "rune_capture"
DEFAULT_RUNE_CAPTURE_OPTIONS = {
    "outputDir": r"protype\RuneInstance",
    "captureIntervalSeconds": 5.0,
}
UNKNOWN_CONFIDENCE_THRESHOLD = 0.30
SLOT_CROP_WIDTH = 76
SLOT_CROP_HEIGHT = 92


@dataclass(frozen=True)
class RuneCaptureResult:
    exit_reason: str
    iterations: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


def run_rune_capture(context: ScriptRunContext) -> RuneCaptureResult:
    output_dir = _resolve_output_dir(context)
    interval_seconds = _coerce_capture_interval(
        context.script_options.get("captureIntervalSeconds", 5.0)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = MssScreenCapture()
    recognizer = RuneCvRecognizer()
    sleeper = Sleeper(logger=context.logger, control=context.control)
    iterations = 0
    last_payload: dict[str, Any] = {}
    window: WindowInfo | None = None

    log_important(
        context.logger,
        "[RuneCapture] start output_dir=%s interval=%.3fs",
        output_dir,
        interval_seconds,
    )

    try:
        while not context.control.stop_requested():
            context.control.wait_if_paused()
            window = refresh_window_info(window, context.config.maple_story.window_title)
            image = capture.capture_region(Region(window.x, window.y, window.width, window.height))
            iterations += 1
            screenshot_path = _next_screenshot_path(output_dir, iterations)
            _write_png(screenshot_path, image)

            prediction = recognizer.recognize(image)
            payload = _payload_for_capture(
                image=image,
                prediction=prediction,
                screenshot_path=screenshot_path,
                output_dir=output_dir,
                interval_seconds=interval_seconds,
                saved_count=iterations,
            )
            last_payload = payload
            context.emit_data(payload)
            context.logger.info(
                "[RuneCapture] saved=%s prediction=%s score=%.3f",
                screenshot_path,
                "".join(DIRECTION_KEYS[item] for item in prediction.predicted),
                prediction.score,
            )
            sleeper.delay_ms(int(interval_seconds * 1000))

        return RuneCaptureResult(
            exit_reason="stop_requested",
            iterations=iterations,
            details=last_payload,
        )
    except StopRequested:
        return RuneCaptureResult(
            exit_reason="stop_requested",
            iterations=iterations,
            details=last_payload,
        )
    finally:
        capture.close()


def _payload_for_capture(
    *,
    image: np.ndarray,
    prediction: RunePrediction,
    screenshot_path: Path,
    output_dir: Path,
    interval_seconds: float,
    saved_count: int,
) -> dict[str, Any]:
    slots = []
    for slot in prediction.slots:
        crop = crop_slot(image, slot.slot_x)
        direction = _label_direction(slot.predicted, slot.confidence, prediction.score)
        slots.append(
            {
                "slot": slot.slot,
                "direction": direction,
                "label": _direction_label(direction),
                "confidence": slot.confidence,
                "rawScore": slot.raw_score,
                "slotX": slot.slot_x,
                "matchX": slot.match_x,
                "matchY": slot.match_y,
                "cropDataUrl": _encode_png_data_url(crop),
            }
        )

    return {
        "kind": "runeCapture",
        "status": "captured",
        "message": f"已保存第 {saved_count} 张截图",
        "outputDir": str(output_dir),
        "capturePath": str(screenshot_path),
        "intervalSeconds": interval_seconds,
        "savedCount": saved_count,
        "predictionScore": prediction.score,
        "groupCenterX": prediction.group_center_x,
        "slots": slots,
        "updatedAt": time.strftime("%H:%M:%S"),
    }


def crop_slot(image: np.ndarray, slot_x: float) -> np.ndarray:
    half_width = SLOT_CROP_WIDTH // 2
    half_height = SLOT_CROP_HEIGHT // 2
    center_x = int(round(slot_x))
    center_y = 240
    left = max(0, center_x - half_width)
    right = min(image.shape[1], center_x + half_width)
    top = max(0, center_y - half_height)
    bottom = min(image.shape[0], center_y + half_height)
    return image[top:bottom, left:right].copy()


def _label_direction(direction: str, confidence: float, group_score: float) -> str:
    if group_score < 1.0 or confidence < UNKNOWN_CONFIDENCE_THRESHOLD:
        return "unknown"
    return direction


def _direction_label(direction: str) -> str:
    return {
        "up": "上",
        "down": "下",
        "left": "左",
        "right": "右",
        "unknown": "unknown",
    }.get(direction, "unknown")


def _encode_png_data_url(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        return ""
    return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def _write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"Could not encode screenshot: {path}")
    path.write_bytes(encoded.tobytes())


def _next_screenshot_path(output_dir: Path, index: int) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return output_dir / f"rune_capture_{stamp}_{index:04d}.png"


def _resolve_output_dir(context: ScriptRunContext) -> Path:
    raw = str(context.script_options.get("outputDir", "")).strip().strip('"').strip("'")
    path = Path(raw) if raw else Path(DEFAULT_RUNE_CAPTURE_OPTIONS["outputDir"])
    if not path.is_absolute():
        path = context.config.project_root / path
    return path.resolve()


def _coerce_capture_interval(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 5.0
    if not np.isfinite(number):
        return 5.0
    return max(0.5, min(3600.0, number))
