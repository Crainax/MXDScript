from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from mhscript_yjs.core.config import external_root
from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.runtime.timing import Sleeper
from mhscript_yjs.scripts.tool.rune_debug import RunePrediction, load_rune_recognizer
from mhscript_yjs.vision.types import Region
from mhscript_yjs.windows.maple import WindowInfo

DEFAULT_RUNE_MAX_ATTEMPTS = 5
DEFAULT_RUNE_OPEN_DELAY_MS = 1000
DEFAULT_RUNE_RETRY_DELAY_MS = 3000
DEFAULT_RUNE_MIN_SCORE = 1.0
DEFAULT_RUNE_MIN_SLOT_CONFIDENCE = 0.50
DEFAULT_RUNE_KEY_INTERVAL_MIN_MS = 90
DEFAULT_RUNE_KEY_INTERVAL_MAX_MS = 130
DEFAULT_RUNE_FAILURE_SCREENSHOT_DIR = Path("auto_screenshots") / "rune_solver"
RUNE_DIRECTION_KEYS = {
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}
RUNE_DIRECTION_LABELS = {
    "up": "上",
    "down": "下",
    "left": "左",
    "right": "右",
}


class RuneCapture(Protocol):
    def capture_region(self, region: Region) -> np.ndarray:
        ...


class RuneRecognizer(Protocol):
    def recognize(self, image: np.ndarray) -> RunePrediction:
        ...


@dataclass(frozen=True)
class RuneSolverConfig:
    max_attempts: int = DEFAULT_RUNE_MAX_ATTEMPTS
    interact_key: str = "PageDown"
    open_delay_ms: int = DEFAULT_RUNE_OPEN_DELAY_MS
    retry_delay_ms: int = DEFAULT_RUNE_RETRY_DELAY_MS
    min_score: float = DEFAULT_RUNE_MIN_SCORE
    min_slot_confidence: float = DEFAULT_RUNE_MIN_SLOT_CONFIDENCE
    key_interval_min_ms: int = DEFAULT_RUNE_KEY_INTERVAL_MIN_MS
    key_interval_max_ms: int = DEFAULT_RUNE_KEY_INTERVAL_MAX_MS
    failure_screenshot_dir: Path = DEFAULT_RUNE_FAILURE_SCREENSHOT_DIR


@dataclass(frozen=True)
class RunePressAttempt:
    status: str
    attempt: int
    reason: str
    directions: tuple[str, ...] = ()
    score: float = 0.0
    slot_confidences: tuple[float, ...] = ()
    screenshot_path: Path | None = None

    @property
    def pressed(self) -> bool:
        return self.status == "pressed"


class RuneSolver:
    def __init__(
        self,
        *,
        device: InputDevice,
        sleeper: Sleeper,
        logger: logging.Logger,
        capture: RuneCapture,
        recognizer: RuneRecognizer | None = None,
        config: RuneSolverConfig | None = None,
    ) -> None:
        self.device = device
        self.sleeper = sleeper
        self.logger = logger
        self.capture = capture
        self.recognizer = recognizer or load_rune_recognizer()
        self.config = config or RuneSolverConfig()
        self.failure_screenshot_dir = _resolve_failure_screenshot_dir(
            self.config.failure_screenshot_dir
        )

    def trigger_and_press(self, window: WindowInfo, *, attempt: int) -> RunePressAttempt:
        self.device.release_all_keys()
        self.device.press_key(keycode(self.config.interact_key), 1)
        self.sleeper.delay_ms(self.config.open_delay_ms)

        image = self.capture.capture_region(
            Region(window.x, window.y, window.width, window.height)
        )
        try:
            prediction = self.recognizer.recognize(image)
        except Exception as exc:
            reason = f"recognizer_error:{exc.__class__.__name__}"
            screenshot_path = self._save_failure_screenshot(
                image,
                attempt=attempt,
                reason=reason,
            )
            log_important(
                self.logger,
                "[解符文] 识别器异常，本次不按方向键；截图=%s，原因=%s",
                screenshot_path,
                reason,
            )
            self.logger.exception("[解符文] 识别器异常")
            self._exit_rune_ui_for_retry()
            return RunePressAttempt(
                status="unrecognized",
                attempt=attempt,
                reason=reason,
                screenshot_path=screenshot_path,
            )

        invalid_reason = self._invalid_prediction_reason(prediction)
        if invalid_reason is not None:
            screenshot_path = self._save_failure_screenshot(
                image,
                attempt=attempt,
                reason=invalid_reason,
            )
            log_important(
                self.logger,
                "[解符文] 识别结果不安全，本次不按方向键；截图=%s，原因=%s，"
                "总分=%.3f，槽位置信度=%s",
                screenshot_path,
                invalid_reason,
                prediction.score,
                _format_confidences(prediction),
            )
            self._exit_rune_ui_for_retry()
            return RunePressAttempt(
                status="unrecognized",
                attempt=attempt,
                reason=invalid_reason,
                score=prediction.score,
                slot_confidences=tuple(slot.confidence for slot in prediction.slots),
                screenshot_path=screenshot_path,
            )

        directions = tuple(prediction.predicted)
        log_important(
            self.logger,
            "[解符文] 已冻结方向=%s，总分=%.3f，槽位置信度=%s，尝试=%s",
            _format_directions(directions),
            prediction.score,
            _format_confidences(prediction),
            attempt,
        )
        for direction in directions:
            self.device.press_key(keycode(RUNE_DIRECTION_KEYS[direction]), 1)
            self.sleeper.delay_random_ms(
                self.config.key_interval_min_ms,
                self.config.key_interval_max_ms,
            )
        return RunePressAttempt(
            status="pressed",
            attempt=attempt,
            reason="pressed_frozen_sequence",
            directions=directions,
            score=prediction.score,
            slot_confidences=tuple(slot.confidence for slot in prediction.slots),
        )

    def _invalid_prediction_reason(self, prediction: RunePrediction) -> str | None:
        if len(prediction.predicted) != 4:
            return f"方向数量不足:{len(prediction.predicted)}"
        if any(direction not in RUNE_DIRECTION_KEYS for direction in prediction.predicted):
            return "存在未知方向"
        if prediction.score < self.config.min_score:
            return f"总分过低:{prediction.score:.3f}"
        low_slots = [
            slot
            for slot in prediction.slots
            if slot.confidence < self.config.min_slot_confidence
        ]
        if low_slots:
            return "槽位置信度过低:" + ",".join(
                f"{slot.slot}:{slot.confidence:.3f}" for slot in low_slots
            )
        return None

    def _exit_rune_ui_for_retry(self) -> None:
        for _ in range(2):
            self.device.press_key(keycode("Space"), 1)
            self.sleeper.delay_ms(120)
        self.sleeper.delay_ms(self.config.retry_delay_ms)

    def _save_failure_screenshot(
        self,
        image: np.ndarray,
        *,
        attempt: int,
        reason: str,
    ) -> Path:
        self.failure_screenshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        safe_reason = _safe_filename(reason)
        filename = f"rune_unrecognized_{stamp}_a{attempt}_{safe_reason}.png"
        path = self.failure_screenshot_dir / filename
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError(f"Could not encode rune failure screenshot: {path}")
        path.write_bytes(encoded.tobytes())
        return path


def _resolve_failure_screenshot_dir(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (external_root() / path).resolve()


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._")[:80] or "unknown"


def _format_directions(directions: tuple[str, ...]) -> str:
    return "".join(RUNE_DIRECTION_LABELS.get(direction, "?") for direction in directions)


def _format_confidences(prediction: RunePrediction) -> str:
    return ",".join(f"{slot.slot}:{slot.confidence:.3f}" for slot in prediction.slots)
