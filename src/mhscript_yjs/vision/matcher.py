from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from logging import Logger
from pathlib import Path
from typing import Protocol

from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region


class ScreenCapture(Protocol):
    def capture_region(self, region: Region):
        ...


class TemplateNotFoundError(FileNotFoundError):
    pass


DEFAULT_PIXEL_COLOR_TOLERANCE = 18
DEFAULT_PIXEL_ALLOWED_BAD_PIXELS = 0
EXACT_PIXEL_WORK_LIMIT = 8_000_000
MAX_RAW_MATCHES = 2000


@dataclass
class TemplateMatcher:
    capture: ScreenCapture
    logger: Logger | None = None
    _cache: dict[Path, tuple[object, object | None]] = field(default_factory=dict)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        start = time.perf_counter()
        haystack = self.capture.capture_region(region)
        matches, best = self._match_group_in(
            haystack,
            group,
            region,
            color_tolerance=DEFAULT_PIXEL_COLOR_TOLERANCE,
            allowed_bad_pixels=DEFAULT_PIXEL_ALLOWED_BAD_PIXELS,
        )
        accepted = matches[0] if matches else None
        self._log_match_any(group, accepted, best, start)
        return accepted

    def match_all(
        self,
        group: ImageGroup,
        region: Region,
        *,
        limit: int = 200,
    ) -> list[MatchResult]:
        start = time.perf_counter()
        haystack = self.capture.capture_region(region)
        return self.match_all_in(haystack, group, region, limit=limit, start=start)

    def match_all_in(
        self,
        haystack: object,
        group: ImageGroup,
        region: Region,
        *,
        limit: int = 200,
        start: float | None = None,
    ) -> list[MatchResult]:
        start = time.perf_counter() if start is None else start
        matches, best = self._match_group_in(
            haystack,
            group,
            region,
            color_tolerance=DEFAULT_PIXEL_COLOR_TOLERANCE,
            allowed_bad_pixels=DEFAULT_PIXEL_ALLOWED_BAD_PIXELS,
        )
        matches = _limit_matches(matches, limit)
        self._log_match_all("findpic_all", group, matches, best, start)
        return matches

    def match_groups(
        self,
        groups: Iterable[ImageGroup],
        region: Region,
        *,
        limit: int = 200,
    ) -> dict[str, list[MatchResult]]:
        start = time.perf_counter()
        haystack = self.capture.capture_region(region)
        results = {
            group.name: self.match_all_in(haystack, group, region, limit=limit)
            for group in groups
        }
        if self.logger:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.logger.debug(
                "findpic_groups groups=%s elapsed_ms=%.2f",
                ",".join(results.keys()),
                elapsed_ms,
            )
        return results

    def match_pixel_any(
        self,
        group: ImageGroup,
        region: Region,
        *,
        color_tolerance: int = DEFAULT_PIXEL_COLOR_TOLERANCE,
        allowed_bad_pixels: int = 2,
    ) -> MatchResult | None:
        matches = self.match_pixel_all(
            group,
            region,
            limit=1,
            color_tolerance=color_tolerance,
            allowed_bad_pixels=allowed_bad_pixels,
        )
        return matches[0] if matches else None

    def match_pixel_all(
        self,
        group: ImageGroup,
        region: Region,
        *,
        limit: int = 200,
        color_tolerance: int = DEFAULT_PIXEL_COLOR_TOLERANCE,
        allowed_bad_pixels: int = 2,
    ) -> list[MatchResult]:
        start = time.perf_counter()
        haystack = self.capture.capture_region(region)
        return self.match_pixel_all_in(
            haystack,
            group,
            region,
            limit=limit,
            color_tolerance=color_tolerance,
            allowed_bad_pixels=allowed_bad_pixels,
            start=start,
        )

    def match_pixel_all_in(
        self,
        haystack: object,
        group: ImageGroup,
        region: Region,
        *,
        limit: int = 200,
        color_tolerance: int = DEFAULT_PIXEL_COLOR_TOLERANCE,
        allowed_bad_pixels: int = 2,
        start: float | None = None,
    ) -> list[MatchResult]:
        start = time.perf_counter() if start is None else start
        matches, best = self._match_group_in(
            haystack,
            group,
            region,
            color_tolerance=color_tolerance,
            allowed_bad_pixels=allowed_bad_pixels,
        )
        matches = _limit_matches(matches, limit)
        self._log_match_all(
            "findpic_pixel_all",
            group,
            matches,
            best,
            start,
            color_tolerance=color_tolerance,
            allowed_bad_pixels=allowed_bad_pixels,
        )
        return matches

    def match_pixel_groups(
        self,
        groups: Iterable[ImageGroup],
        region: Region,
        *,
        limit: int = 200,
        color_tolerance: int = DEFAULT_PIXEL_COLOR_TOLERANCE,
        allowed_bad_pixels: int = 2,
    ) -> dict[str, list[MatchResult]]:
        start = time.perf_counter()
        haystack = self.capture.capture_region(region)
        results = {
            group.name: self.match_pixel_all_in(
                haystack,
                group,
                region,
                limit=limit,
                color_tolerance=color_tolerance,
                allowed_bad_pixels=allowed_bad_pixels,
            )
            for group in groups
        }
        if self.logger:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.logger.debug(
                "findpic_pixel_groups groups=%s color_tolerance=%s allowed_bad_pixels=%s elapsed_ms=%.2f",
                ",".join(results.keys()),
                color_tolerance,
                allowed_bad_pixels,
                elapsed_ms,
            )
        return results

    def _match_group_in(
        self,
        haystack: object,
        group: ImageGroup,
        region: Region,
        *,
        color_tolerance: int,
        allowed_bad_pixels: int,
    ) -> tuple[list[MatchResult], MatchResult | None]:
        matches: list[MatchResult] = []
        best: MatchResult | None = None
        for image_path in group.paths:
            template, mask = self._load_template(image_path)
            image_matches, image_best = self._match_pixels_many_with_best(
                haystack=haystack,
                template=template,
                mask=mask,
                region=region,
                group=group.name,
                image_path=image_path,
                threshold=group.threshold,
                color_tolerance=color_tolerance,
                allowed_bad_pixels=allowed_bad_pixels,
            )
            matches.extend(image_matches)
            if image_best is not None and (best is None or image_best.score > best.score):
                best = image_best

        matches.sort(key=lambda item: (-item.score, item.y, item.x, str(item.image_path)))
        return matches, best

    def _load_template(self, image_path: Path) -> tuple[object, object | None]:
        image_path = image_path.resolve()
        if image_path in self._cache:
            return self._cache[image_path]
        if not image_path.exists():
            raise TemplateNotFoundError(f"Template image not found: {image_path}")

        import cv2
        import numpy as np

        # cv2.imread cannot reliably open Unicode/reparse-point paths on Windows.
        # Reading bytes through Python first keeps paths such as OneDrive Chinese folders working.
        raw_template = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
        if raw_template is None:
            raise TemplateNotFoundError(f"Template image could not be read: {image_path}")

        mask = None
        if raw_template.ndim == 2:
            template = np.repeat(raw_template[:, :, None], 3, axis=2)
        elif raw_template.shape[2] == 4:
            template = raw_template[:, :, :3]
            alpha = raw_template[:, :, 3]
            if np.any(alpha < 255):
                mask = alpha > 0
        else:
            template = raw_template[:, :, :3]

        self._cache[image_path] = (template, mask)
        return template, mask

    def _match_pixels_many_with_best(
        self,
        *,
        haystack: object,
        template: object,
        mask: object | None,
        region: Region,
        group: str,
        image_path: Path,
        threshold: float,
        color_tolerance: int,
        allowed_bad_pixels: int,
    ) -> tuple[list[MatchResult], MatchResult | None]:
        import numpy as np

        haystack_array = _ensure_bgr_array(haystack)
        template_array = _ensure_bgr_array(template)
        template_height, template_width = template_array.shape[:2]
        haystack_height, haystack_width = haystack_array.shape[:2]
        if template_width > haystack_width or template_height > haystack_height:
            if self.logger:
                self.logger.warning(
                    "template_larger_than_region group=%s image=%s template=%sx%s region=%sx%s",
                    group,
                    image_path,
                    template_width,
                    template_height,
                    haystack_width,
                    haystack_height,
                )
            return [], None

        valid_mask = np.ones((template_height, template_width), dtype=bool)
        if mask is not None:
            valid_mask = np.asarray(mask, dtype=bool)
        valid_count = int(np.count_nonzero(valid_mask))
        if valid_count <= 0:
            return [], None

        score_height = haystack_height - template_height + 1
        score_width = haystack_width - template_width + 1
        work_units = score_height * score_width * valid_count
        if work_units <= EXACT_PIXEL_WORK_LIMIT:
            return self._match_pixels_exact(
                haystack=haystack_array,
                template=template_array,
                valid_mask=valid_mask,
                region=region,
                group=group,
                image_path=image_path,
                threshold=threshold,
                color_tolerance=color_tolerance,
                allowed_bad_pixels=allowed_bad_pixels,
            )
        return self._match_pixels_candidates(
            haystack=haystack_array,
            template=template_array,
            valid_mask=valid_mask,
            region=region,
            group=group,
            image_path=image_path,
            threshold=threshold,
            color_tolerance=color_tolerance,
            allowed_bad_pixels=allowed_bad_pixels,
        )

    def _match_pixels_exact(
        self,
        *,
        haystack: object,
        template: object,
        valid_mask: object,
        region: Region,
        group: str,
        image_path: Path,
        threshold: float,
        color_tolerance: int,
        allowed_bad_pixels: int,
    ) -> tuple[list[MatchResult], MatchResult | None]:
        import numpy as np

        haystack_array = np.asarray(haystack)
        template_array = np.asarray(template)
        valid_mask_array = np.asarray(valid_mask, dtype=bool)
        template_height, template_width = template_array.shape[:2]
        valid_count = int(np.count_nonzero(valid_mask_array))
        windows = np.lib.stride_tricks.sliding_window_view(
            haystack_array,
            (template_height, template_width),
            axis=(0, 1),
        )
        windows = np.moveaxis(windows, 2, -1)
        diff = np.abs(windows.astype(np.int16) - template_array.astype(np.int16))
        pixel_ok = np.all(diff <= max(0, int(color_tolerance)), axis=-1)
        good_counts = pixel_ok[..., valid_mask_array].sum(axis=-1)
        misses = valid_count - good_counts
        scores = good_counts.astype(np.float32) / float(valid_count)

        best_y, best_x = (int(item) for item in np.unravel_index(np.argmax(scores), scores.shape))
        best = _match_result(
            group,
            image_path,
            region,
            best_x,
            best_y,
            template_width,
            template_height,
            float(scores[best_y, best_x]),
        )
        accepted = (scores >= threshold - 1e-9) | (misses <= max(0, int(allowed_bad_pixels)))
        return _matches_from_score_arrays(
            accepted=accepted,
            scores=scores,
            region=region,
            group=group,
            image_path=image_path,
            width=template_width,
            height=template_height,
        ), best

    def _match_pixels_candidates(
        self,
        *,
        haystack: object,
        template: object,
        valid_mask: object,
        region: Region,
        group: str,
        image_path: Path,
        threshold: float,
        color_tolerance: int,
        allowed_bad_pixels: int,
    ) -> tuple[list[MatchResult], MatchResult | None]:
        import numpy as np

        haystack_array = np.asarray(haystack)
        template_array = np.asarray(template)
        valid_mask_array = np.asarray(valid_mask, dtype=bool)
        template_height, template_width = template_array.shape[:2]
        haystack_height, haystack_width = haystack_array.shape[:2]
        score_height = haystack_height - template_height + 1
        score_width = haystack_width - template_width + 1
        valid_y, valid_x = np.nonzero(valid_mask_array)
        valid_template = template_array[valid_y, valid_x].astype(np.int16)
        valid_count = int(valid_template.shape[0])
        anchors = _select_anchor_coords(valid_mask_array, template_width, template_height)
        anchor_good_counts = np.zeros((score_height, score_width), dtype=np.uint8)
        tolerance = max(0, int(color_tolerance))

        for anchor_y, anchor_x in anchors:
            haystack_slice = haystack_array[
                anchor_y : anchor_y + score_height,
                anchor_x : anchor_x + score_width,
            ]
            anchor_color = template_array[anchor_y, anchor_x].astype(np.int16)
            ok = np.all(np.abs(haystack_slice.astype(np.int16) - anchor_color) <= tolerance, axis=-1)
            anchor_good_counts += ok.astype(np.uint8)

        anchor_count = max(1, len(anchors))
        threshold_required = int(math.ceil(max(0.0, min(1.0, threshold)) * anchor_count - 1e-9))
        bad_pixel_required = anchor_count - max(0, int(allowed_bad_pixels))
        required_anchor_matches = max(1, min(threshold_required, bad_pixel_required))
        candidate_y, candidate_x = np.nonzero(anchor_good_counts >= required_anchor_matches)
        if len(candidate_x) == 0:
            best_y, best_x = (
                int(item) for item in np.unravel_index(np.argmax(anchor_good_counts), anchor_good_counts.shape)
            )
            best_score = float(anchor_good_counts[best_y, best_x]) / float(anchor_count)
            return [], _match_result(
                group,
                image_path,
                region,
                best_x,
                best_y,
                template_width,
                template_height,
                best_score,
            )

        scores = np.zeros(len(candidate_x), dtype=np.float32)
        misses = np.zeros(len(candidate_x), dtype=np.int32)
        chunk_size = max(1, min(2048, 2_000_000 // max(1, valid_count)))
        for offset in range(0, len(candidate_x), chunk_size):
            ys = candidate_y[offset : offset + chunk_size]
            xs = candidate_x[offset : offset + chunk_size]
            pixels = haystack_array[ys[:, None] + valid_y, xs[:, None] + valid_x].astype(np.int16)
            ok = np.all(np.abs(pixels - valid_template) <= tolerance, axis=-1)
            good_counts = ok.sum(axis=1)
            scores[offset : offset + len(xs)] = good_counts.astype(np.float32) / float(valid_count)
            misses[offset : offset + len(xs)] = valid_count - good_counts

        best_index = int(np.argmax(scores))
        best = _match_result(
            group,
            image_path,
            region,
            int(candidate_x[best_index]),
            int(candidate_y[best_index]),
            template_width,
            template_height,
            float(scores[best_index]),
        )
        accepted_mask = (scores >= threshold - 1e-9) | (misses <= max(0, int(allowed_bad_pixels)))
        accepted_indices = np.flatnonzero(accepted_mask)
        if len(accepted_indices) == 0:
            return [], best

        order = accepted_indices[np.argsort(-scores[accepted_indices])]
        results: list[MatchResult] = []
        for index in order:
            x = int(candidate_x[index])
            y = int(candidate_y[index])
            if _is_suppressed(
                results,
                region.x + x,
                region.y + y,
                template_width,
                template_height,
            ):
                continue
            results.append(
                _match_result(
                    group,
                    image_path,
                    region,
                    x,
                    y,
                    template_width,
                    template_height,
                    float(scores[index]),
                )
            )
            if len(results) >= MAX_RAW_MATCHES:
                break
        return results, best

    def _log_match_any(
        self,
        group: ImageGroup,
        accepted: MatchResult | None,
        best: MatchResult | None,
        start: float,
    ) -> None:
        if not self.logger:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        if accepted:
            self.logger.debug(
                "findpic group=%s accepted=%s x=%s y=%s score=%.9f threshold=%.6f elapsed_ms=%.2f",
                group.name,
                accepted.image_path,
                accepted.x,
                accepted.y,
                accepted.score,
                group.threshold,
                elapsed_ms,
            )
        elif best:
            self.logger.debug(
                "findpic group=%s below_threshold=%s x=%s y=%s score=%.9f threshold=%.6f elapsed_ms=%.2f",
                group.name,
                best.image_path,
                best.x,
                best.y,
                best.score,
                group.threshold,
                elapsed_ms,
            )
        else:
            self.logger.debug(
                "findpic group=%s no_match threshold=%.6f elapsed_ms=%.2f",
                group.name,
                group.threshold,
                elapsed_ms,
            )

    def _log_match_all(
        self,
        prefix: str,
        group: ImageGroup,
        matches: list[MatchResult],
        best: MatchResult | None,
        start: float,
        *,
        color_tolerance: int | None = None,
        allowed_bad_pixels: int | None = None,
    ) -> None:
        if not self.logger:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        extra = ""
        if color_tolerance is not None and allowed_bad_pixels is not None:
            extra = (
                f" color_tolerance={color_tolerance}"
                f" allowed_bad_pixels={allowed_bad_pixels}"
            )
        if best is not None:
            self.logger.debug(
                "%s group=%s count=%s best=%s x=%s y=%s best_score=%.9f threshold=%.6f%s elapsed_ms=%.2f",
                prefix,
                group.name,
                len(matches),
                best.image_path,
                best.x,
                best.y,
                best.score,
                group.threshold,
                extra,
                elapsed_ms,
            )
        else:
            self.logger.debug(
                "%s group=%s count=%s no_match threshold=%.6f%s elapsed_ms=%.2f",
                prefix,
                group.name,
                len(matches),
                group.threshold,
                extra,
                elapsed_ms,
            )


def _ensure_bgr_array(image: object):
    import numpy as np

    array = np.asarray(image)
    if array.ndim == 2:
        return np.repeat(array[:, :, None], 3, axis=2)
    if array.ndim == 3 and array.shape[2] >= 3:
        return array[:, :, :3]
    raise ValueError(f"Unsupported image array shape: {array.shape}")


def _limit_matches(matches: list[MatchResult], limit: int) -> list[MatchResult]:
    if limit <= 0:
        return matches
    return matches[:limit]


def _matches_from_score_arrays(
    *,
    accepted: object,
    scores: object,
    region: Region,
    group: str,
    image_path: Path,
    width: int,
    height: int,
) -> list[MatchResult]:
    import numpy as np

    accepted_array = np.asarray(accepted, dtype=bool)
    score_array = np.asarray(scores)
    ys, xs = np.nonzero(accepted_array)
    if len(xs) == 0:
        return []
    order = np.argsort(-score_array[ys, xs])
    results: list[MatchResult] = []
    for index in order:
        x = int(xs[index])
        y = int(ys[index])
        if _is_suppressed(results, region.x + x, region.y + y, width, height):
            continue
        results.append(
            _match_result(
                group,
                image_path,
                region,
                x,
                y,
                width,
                height,
                float(score_array[y, x]),
            )
        )
        if len(results) >= MAX_RAW_MATCHES:
            break
    return results


def _select_anchor_coords(valid_mask: object, width: int, height: int) -> list[tuple[int, int]]:
    import numpy as np

    valid_mask_array = np.asarray(valid_mask, dtype=bool)
    valid_y, valid_x = np.nonzero(valid_mask_array)
    if len(valid_x) == 0:
        return []
    target_points = (
        (0, 0),
        (0, width - 1),
        (height - 1, 0),
        (height - 1, width - 1),
        (height // 2, width // 2),
        (height // 2, 0),
        (0, width // 2),
        (height // 2, width - 1),
        (height - 1, width // 2),
    )
    anchors: list[tuple[int, int]] = []
    for target_y, target_x in target_points:
        distances = (valid_y - target_y) ** 2 + (valid_x - target_x) ** 2
        index = int(np.argmin(distances))
        anchor = (int(valid_y[index]), int(valid_x[index]))
        if anchor not in anchors:
            anchors.append(anchor)
    return anchors or [(int(valid_y[0]), int(valid_x[0]))]


def _is_suppressed(
    existing: list[MatchResult],
    absolute_x: int,
    absolute_y: int,
    width: int,
    height: int,
) -> bool:
    for match in existing:
        if abs(absolute_x - match.x) < width and abs(absolute_y - match.y) < height:
            return True
    return False


def _match_result(
    group: str,
    image_path: Path,
    region: Region,
    x: int,
    y: int,
    width: int,
    height: int,
    score: float,
) -> MatchResult:
    return MatchResult(
        group=group,
        image_path=image_path,
        x=region.x + int(x),
        y=region.y + int(y),
        width=int(width),
        height=int(height),
        score=float(score),
    )
