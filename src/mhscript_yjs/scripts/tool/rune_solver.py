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
DEFAULT_RUNE_OPEN_DELAY_MS = 500
DEFAULT_RUNE_OPEN_POLL_SAMPLES = 6
DEFAULT_RUNE_RETRY_DELAY_MS = 3000
DEFAULT_RUNE_MIN_SCORE = 1.0
DEFAULT_RUNE_MIN_SLOT_CONFIDENCE = 0.50
DEFAULT_RUNE_UI_MISSING_SCORE = 0.25
DEFAULT_RUNE_UI_MISSING_SLOT_CONFIDENCE = 0.25
DEFAULT_RUNE_UI_MISSING_SELECTION_SCORE = 0.35
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
    open_poll_samples: int = DEFAULT_RUNE_OPEN_POLL_SAMPLES
    retry_delay_ms: int = DEFAULT_RUNE_RETRY_DELAY_MS
    min_score: float = DEFAULT_RUNE_MIN_SCORE
    min_slot_confidence: float = DEFAULT_RUNE_MIN_SLOT_CONFIDENCE
    ui_missing_score: float = DEFAULT_RUNE_UI_MISSING_SCORE
    ui_missing_slot_confidence: float = DEFAULT_RUNE_UI_MISSING_SLOT_CONFIDENCE
    ui_missing_selection_score: float = DEFAULT_RUNE_UI_MISSING_SELECTION_SCORE
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
        return self._trigger_and_press_with_polling(window, attempt=attempt)

    def _trigger_and_press_with_polling(self, window: WindowInfo, *, attempt: int) -> RunePressAttempt:
        self.device.release_all_keys()
        self.device.press_key(keycode(self.config.interact_key), 1)
        window_region = Region(window.x, window.y, window.width, window.height)
        poll_samples = max(1, int(self.config.open_poll_samples))
        poll_interval_ms = max(1, int(self.config.open_delay_ms))
        last_missing: tuple[np.ndarray, RunePrediction, str, int] | None = None
        best_unsafe: tuple[np.ndarray, RunePrediction, str, int] | None = None

        for sample in range(1, poll_samples + 1):
            self.sleeper.delay_ms(poll_interval_ms)
            image = self.capture.capture_region(window_region)
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
                    "[解符文] 识别器异常，本次不按方向键；截图=%s，原因=%s，帧=%s/%s",
                    screenshot_path,
                    reason,
                    sample,
                    poll_samples,
                )
                self.logger.exception("[解符文] 识别器异常")
                self._exit_rune_ui_for_retry()
                return RunePressAttempt(
                    status="unrecognized",
                    attempt=attempt,
                    reason=reason,
                    screenshot_path=screenshot_path,
                )

            ui_missing_reason = self._ui_missing_reason(prediction)
            if ui_missing_reason is not None:
                last_missing = (image, prediction, ui_missing_reason, sample)
                self.logger.info(
                    "[解符文] 第 %s/%s 帧未检测到符文UI/箭头，继续等待；原因=%s score=%.3f confidence=%s",
                    sample,
                    poll_samples,
                    ui_missing_reason,
                    prediction.score,
                    _format_confidences(prediction),
                )
                continue

            invalid_reason = self._invalid_prediction_reason(prediction)
            if invalid_reason is not None:
                if best_unsafe is None or prediction.score >= best_unsafe[1].score:
                    best_unsafe = (image, prediction, invalid_reason, sample)
                self.logger.info(
                    "[解符文] 第 %s/%s 帧检测到符文UI但识别不安全，继续观察；原因=%s score=%.3f confidence=%s",
                    sample,
                    poll_samples,
                    invalid_reason,
                    prediction.score,
                    _format_confidences(prediction),
                )
                continue

            directions = tuple(prediction.predicted)
            log_important(
                self.logger,
                "[解符文] 第 %s/%s 帧安全识别，冻结方向=%s，总分=%.3f，槽位置信度=%s，尝试=%s",
                sample,
                poll_samples,
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

        if best_unsafe is not None:
            image, prediction, invalid_reason, sample = best_unsafe
            screenshot_path = self._save_failure_screenshot(
                image,
                attempt=attempt,
                reason=invalid_reason,
            )
            log_important(
                self.logger,
                "[解符文] %s 帧内符文UI已出现但识别结果不安全，本次不按方向键；"
                "选用第 %s 帧截图=%s，原因=%s，总分=%.3f，槽位置信度=%s",
                poll_samples,
                sample,
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

        if last_missing is not None:
            image, prediction, ui_missing_reason, sample = last_missing
        else:
            image = self.capture.capture_region(window_region)
            try:
                prediction = self.recognizer.recognize(image)
                ui_missing_reason = self._ui_missing_reason(prediction) or "rune_ui_missing:no_poll_result"
            except Exception as exc:
                prediction = None
                ui_missing_reason = f"rune_ui_missing:no_poll_result:{exc.__class__.__name__}"
            sample = poll_samples
        screenshot_path = self._save_failure_screenshot(
            image,
            attempt=attempt,
            reason=ui_missing_reason,
        )
        log_important(
            self.logger,
            "[解符文] %s 帧内未检测到符文UI/箭头，视为站位不可交互；保存第 %s 帧截图=%s，原因=%s%s",
            poll_samples,
            sample,
            screenshot_path,
            ui_missing_reason,
            (
                f"，总分={prediction.score:.3f}，槽位置信度={_format_confidences(prediction)}"
                if prediction is not None
                else ""
            ),
        )
        return RunePressAttempt(
            status="ui_missing",
            attempt=attempt,
            reason=ui_missing_reason,
            score=prediction.score if prediction is not None else 0.0,
            slot_confidences=(
                tuple(slot.confidence for slot in prediction.slots)
                if prediction is not None
                else ()
            ),
            screenshot_path=screenshot_path,
        )

    def _trigger_and_press_once(self, window: WindowInfo, *, attempt: int) -> RunePressAttempt:
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

        ui_missing_reason = self._ui_missing_reason(prediction)
        if ui_missing_reason is not None:
            screenshot_path = self._save_failure_screenshot(
                image,
                attempt=attempt,
                reason=ui_missing_reason,
            )
            log_important(
                self.logger,
                "[解符文] PageDown 后未检测到符文UI/箭头，视为站位不可交互；截图=%s，原因=%s，"
                "总分=%.3f，槽位置信度=%s",
                screenshot_path,
                ui_missing_reason,
                prediction.score,
                _format_confidences(prediction),
            )
            return RunePressAttempt(
                status="ui_missing",
                attempt=attempt,
                reason=ui_missing_reason,
                score=prediction.score,
                slot_confidences=tuple(slot.confidence for slot in prediction.slots),
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

    def _ui_missing_reason(self, prediction: RunePrediction) -> str | None:
        selection_score = prediction.selection_score
        if (
            selection_score is not None
            and selection_score < self.config.ui_missing_selection_score
        ):
            return f"rune_ui_missing:selection_score={selection_score:.3f}"

        max_slot_confidence = max((slot.confidence for slot in prediction.slots), default=0.0)
        if (
            prediction.score < self.config.ui_missing_score
            and max_slot_confidence < self.config.ui_missing_slot_confidence
        ):
            return (
                f"rune_ui_missing:score={prediction.score:.3f},"
                f"max_slot={max_slot_confidence:.3f}"
            )
        return None

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
