from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhscript_yjs.core.config import ProjectConfig
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.vision.matcher import TemplateMatcher, TemplateNotFoundError
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo, find_window, refresh_window_info

if TYPE_CHECKING:
    from mhscript_yjs.scripts.registry import ScriptRunContext


IMAGE_RECOGNITION_SCRIPT_ID = "image_recognition"
COORDINATE_DETECTOR_SCRIPT_ID = "coordinate_detector"
DEFAULT_IMAGE_RECOGNITION_OPTIONS = {
    "imagePath": "",
    "matchThreshold": 0.95,
    "intervalSeconds": 0.5,
}
DEFAULT_COORDINATE_DETECTOR_OPTIONS = {
    "matchThreshold": 0.95,
    "intervalSeconds": 0.5,
}
MAX_DISPLAY_MATCHES = 500
COORDINATE_PIXEL_COLOR_TOLERANCE = 18
COORDINATE_PIXEL_ALLOWED_BAD_PIXELS = 2
CACHED_MISS_LIMIT = 3


@dataclass(frozen=True)
class ImageDebugResult:
    exit_reason: str
    iterations: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


def run_image_recognition(context: ScriptRunContext) -> ImageDebugResult:
    image_path_text = str(context.script_options.get("imagePath", "")).strip()
    threshold = _coerce_threshold(context.script_options.get("matchThreshold", 0.95))
    interval_seconds = _coerce_interval(context.script_options.get("intervalSeconds", 0.5))
    if not image_path_text:
        context.emit_data(
            {
                "kind": "imageRecognition",
                "status": "missingPath",
                "message": "未填写图片路径",
                "matches": [],
                "threshold": threshold,
                "intervalSeconds": interval_seconds,
            }
        )
        log_important(context.logger, "[识别图片] 未填写图片路径")
        return ImageDebugResult(exit_reason="missing_image_path")

    image_path = _resolve_image_path(context.config, image_path_text)
    if not image_path.exists():
        context.emit_data(
            {
                "kind": "imageRecognition",
                "status": "missingFile",
                "message": f"图片不存在：{image_path}",
                "imagePath": str(image_path),
                "matches": [],
                "threshold": threshold,
                "intervalSeconds": interval_seconds,
            }
        )
        log_important(context.logger, "[识别图片] 图片不存在：%s", image_path)
        return ImageDebugResult(exit_reason="missing_image_file")

    capture = MssScreenCapture()
    iterations = 0
    last_count = 0
    try:
        window: WindowInfo | None = None
        matcher = TemplateMatcher(capture=capture, logger=context.logger)
        group = ImageGroup(name=image_path.name, paths=(image_path,), threshold=threshold)
        log_important(
            context.logger,
            "[识别图片] 开始检测：%s，容忍度=%.3f，间隔=%.3fs",
            image_path,
            threshold,
            interval_seconds,
        )

        while not context.control.stop_requested():
            context.control.wait_if_paused()
            window = refresh_window_info(window, context.config.maple_story.window_title)
            region = _window_region(window)
            try:
                matches = matcher.match_all(group, region, limit=MAX_DISPLAY_MATCHES)
            except TemplateNotFoundError as exc:
                context.emit_data(
                    {
                        "kind": "imageRecognition",
                        "status": "missingFile",
                        "message": str(exc),
                        "imagePath": str(image_path),
                        "matches": [],
                        "threshold": threshold,
                        "intervalSeconds": interval_seconds,
                    }
                )
                return ImageDebugResult(exit_reason="missing_image_file", iterations=iterations)

            last_count = len(matches)
            payload = {
                "kind": "imageRecognition",
                "status": "matched" if matches else "notMatched",
                "message": f"识别到 {len(matches)} 个匹配" if matches else "未识别",
                "imagePath": str(image_path),
                "threshold": threshold,
                "intervalSeconds": interval_seconds,
                "matches": [_match_payload(match) for match in matches],
                "updatedAt": time.strftime("%H:%M:%S"),
            }
            context.emit_data(payload)
            context.logger.info(
                "[识别图片] %s count=%s threshold=%.3f",
                "matched" if matches else "not_matched",
                len(matches),
                threshold,
            )
            iterations += 1
            if not _sleep_with_control(context, interval_seconds):
                break

        return ImageDebugResult(
            exit_reason="stop_requested",
            iterations=iterations,
            details={"lastCount": last_count},
        )
    finally:
        capture.close()


def run_coordinate_detector(context: ScriptRunContext) -> ImageDebugResult:
    threshold = _coerce_threshold(context.script_options.get("matchThreshold", 0.95))
    interval_seconds = _coerce_interval(context.script_options.get("intervalSeconds", 0.5))
    image_root = context.config.maple_story.image_root
    image_paths = {
        "me": image_root / "Me.bmp",
        "anchor": image_root / "MapAnchor.bmp",
        "teleport": image_root / "Teleport.bmp",
        "rune": image_root / "Rune.bmp",
    }

    capture = MssScreenCapture()
    iterations = 0
    last_people_count = 0
    last_teleport_count = 0
    last_rune_count = 0
    try:
        window: WindowInfo | None = None
        last_window_key: tuple[int, int, int, int] | None = None
        last_anchor: MatchResult | None = None
        anchor_miss_count = 0
        cached_matches: dict[str, list[MatchResult]] = {"people": [], "teleports": [], "runes": []}
        cached_misses: dict[str, int] = {"people": 0, "teleports": 0, "runes": 0}
        matcher = TemplateMatcher(capture=capture, logger=context.logger)
        log_important(
            context.logger,
            "[检测坐标] 开始检测，容忍度=%.3f，间隔=%.3fs，逐像素容差=%s，允许坏点=%s",
            threshold,
            interval_seconds,
            COORDINATE_PIXEL_COLOR_TOLERANCE,
            COORDINATE_PIXEL_ALLOWED_BAD_PIXELS,
        )

        while not context.control.stop_requested():
            context.control.wait_if_paused()
            window = refresh_window_info(window, context.config.maple_story.window_title)
            window_key = (window.x, window.y, window.width, window.height)
            if last_window_key is not None and window_key != last_window_key:
                last_anchor = None
                anchor_miss_count = 0
                cached_matches = {"people": [], "teleports": [], "runes": []}
                cached_misses = {"people": 0, "teleports": 0, "runes": 0}
            last_window_key = window_key
            region = Region.from_bounds(window.x, window.y, window.x + 400, window.y + 330)
            missing = [str(path) for path in image_paths.values() if not path.exists()]
            if not image_paths["anchor"].exists():
                context.emit_data(
                    {
                        "kind": "coordinateDetector",
                        "status": "missingFile",
                        "message": "缺少图片文件",
                        "missingImages": missing,
                        "people": [],
                        "teleports": [],
                        "runes": [],
                        "threshold": threshold,
                        "intervalSeconds": interval_seconds,
                        "updatedAt": time.strftime("%H:%M:%S"),
                    }
                )
                iterations += 1
                if not _sleep_with_control(context, interval_seconds):
                    break
                continue

            groups = [
                ImageGroup(name, (image_paths[key],), threshold)
                for key, name in (
                    ("anchor", "MapAnchor.bmp"),
                    ("me", "Me.bmp"),
                    ("teleport", "Teleport.bmp"),
                    ("rune", "Rune.bmp"),
                )
                if image_paths[key].exists()
            ]
            matches_by_name = matcher.match_pixel_groups(
                groups,
                region,
                limit=MAX_DISPLAY_MATCHES,
                color_tolerance=COORDINATE_PIXEL_COLOR_TOLERANCE,
                allowed_bad_pixels=COORDINATE_PIXEL_ALLOWED_BAD_PIXELS,
            )
            anchor_matches = matches_by_name.get("MapAnchor.bmp", [])[:10]
            if not anchor_matches:
                anchor_miss_count += 1
                if last_anchor is not None and anchor_miss_count < CACHED_MISS_LIMIT:
                    anchor = last_anchor
                    anchor_status = f"cached:{anchor_miss_count}"
                else:
                    last_anchor = None
                    anchor_status = "lost"
                    context.emit_data(
                        {
                            "kind": "coordinateDetector",
                            "status": "noAnchor",
                            "anchorStatus": anchor_status,
                            "message": "未识别 MapAnchor",
                            "missingImages": missing,
                            "people": [],
                            "teleports": [],
                            "runes": [],
                            "threshold": threshold,
                            "intervalSeconds": interval_seconds,
                            "updatedAt": time.strftime("%H:%M:%S"),
                        }
                    )
                    iterations += 1
                    if not _sleep_with_control(context, interval_seconds):
                        break
                    continue
            else:
                anchor = anchor_matches[0]
                last_anchor = anchor
                anchor_miss_count = 0
                anchor_status = "matched"

            people, people_status = _with_short_cache(
                "people",
                matches_by_name.get("Me.bmp", []),
                cached_matches,
                cached_misses,
            )
            teleports, teleport_status = _with_short_cache(
                "teleports",
                matches_by_name.get("Teleport.bmp", []),
                cached_matches,
                cached_misses,
            )
            runes, rune_status = _with_short_cache(
                "runes",
                matches_by_name.get("Rune.bmp", []),
                cached_matches,
                cached_misses,
            )
            last_people_count = len(people)
            last_teleport_count = len(teleports)
            last_rune_count = len(runes)
            context.emit_data(
                {
                    "kind": "coordinateDetector",
                    "status": "matched",
                    "anchorStatus": anchor_status,
                    "message": "坐标已更新" if not missing else "坐标已更新，部分图片缺失",
                    "missingImages": missing,
                    "threshold": threshold,
                    "intervalSeconds": interval_seconds,
                    "anchor": _match_payload(anchor),
                    "people": [_relative_payload(match, anchor) for match in people],
                    "teleports": [_relative_payload(match, anchor) for match in teleports],
                    "runes": [_relative_payload(match, anchor) for match in runes],
                    "peopleStatus": people_status,
                    "teleportStatus": teleport_status,
                    "runeStatus": rune_status,
                    "updatedAt": time.strftime("%H:%M:%S"),
                }
            )
            context.logger.info(
                "[检测坐标] people=%s teleports=%s runes=%s anchor=(%s,%s) anchor_status=%s people_status=%s teleport_status=%s rune_status=%s",
                len(people),
                len(teleports),
                len(runes),
                anchor.x,
                anchor.y,
                anchor_status,
                people_status,
                teleport_status,
                rune_status,
            )
            iterations += 1
            if not _sleep_with_control(context, interval_seconds):
                break

        return ImageDebugResult(
            exit_reason="stop_requested",
            iterations=iterations,
            details={
                "lastPeopleCount": last_people_count,
                "lastTeleportCount": last_teleport_count,
                "lastRuneCount": last_rune_count,
            },
        )
    finally:
        capture.close()


def _resolve_image_path(config: ProjectConfig, raw_path: str) -> Path:
    cleaned = raw_path.strip().strip('"').strip("'")
    path = Path(cleaned)
    if path.is_absolute():
        return path
    normalized = cleaned.replace("/", "\\")
    if normalized.lower().startswith("assets\\"):
        return config.project_root / normalized
    return config.maple_story.image_root / normalized


def _window_region(window: WindowInfo) -> Region:
    return Region.from_bounds(window.x, window.y, window.right, window.bottom)


def _coerce_threshold(value: Any) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        threshold = 0.95
    if not math.isfinite(threshold):
        threshold = 0.95
    return max(0.0, min(1.0, threshold))


def _coerce_interval(value: Any) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        interval = 0.5
    if not math.isfinite(interval):
        interval = 0.5
    return max(0.05, min(10.0, interval))


def _match_payload(match: MatchResult) -> dict[str, Any]:
    return {
        "x": match.x,
        "y": match.y,
        "centerX": match.center_x,
        "centerY": match.center_y,
        "width": match.width,
        "height": match.height,
        "score": round(match.score, 6),
        "imagePath": str(match.image_path),
    }


def _relative_payload(match: MatchResult, anchor: MatchResult) -> dict[str, Any]:
    payload = _match_payload(match)
    payload["relativeX"] = match.x - anchor.x
    payload["relativeY"] = match.y - anchor.y
    payload["relativeCenterX"] = match.center_x - anchor.x
    payload["relativeCenterY"] = match.center_y - anchor.y
    return payload


def _with_short_cache(
    key: str,
    matches: list[MatchResult],
    cached_matches: dict[str, list[MatchResult]],
    cached_misses: dict[str, int],
) -> tuple[list[MatchResult], str]:
    if matches:
        cached_matches[key] = matches
        cached_misses[key] = 0
        return matches, "matched"
    cached_misses[key] += 1
    if cached_matches[key] and cached_misses[key] < CACHED_MISS_LIMIT:
        return cached_matches[key], f"cached:{cached_misses[key]}"
    cached_matches[key] = []
    return [], "lost"


def _sleep_with_control(context: ScriptRunContext, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if context.control.stop_requested():
            return False
        context.control.wait_if_paused()
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return not context.control.stop_requested()
