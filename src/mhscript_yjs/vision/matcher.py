from __future__ import annotations

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


@dataclass
class TemplateMatcher:
    capture: ScreenCapture
    logger: Logger | None = None
    _cache: dict[Path, tuple[object, object | None]] = field(default_factory=dict)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        start = time.perf_counter()
        haystack = self.capture.capture_region(region)
        best: MatchResult | None = None

        for image_path in group.paths:
            template, mask = self._load_template(image_path)
            result = self._match_one(
                haystack=haystack,
                template=template,
                mask=mask,
                region=region,
                group=group.name,
                image_path=image_path,
            )
            if result and (best is None or result.score > best.score):
                best = result

        elapsed_ms = (time.perf_counter() - start) * 1000
        accepted = best if best and best.score >= group.threshold - 1e-9 else None
        if self.logger:
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
        matches: list[MatchResult] = []
        best: MatchResult | None = None

        for image_path in group.paths:
            template, mask = self._load_template(image_path)
            image_matches, image_best = self._match_many_with_best(
                haystack=haystack,
                template=template,
                mask=mask,
                region=region,
                group=group.name,
                image_path=image_path,
                threshold=group.threshold,
            )
            matches.extend(image_matches)
            if image_best is not None and (best is None or image_best.score > best.score):
                best = image_best

        matches.sort(key=lambda item: (-item.score, item.y, item.x, str(item.image_path)))
        if limit > 0:
            matches = matches[:limit]

        if self.logger:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if best is not None:
                self.logger.debug(
                    "findpic_all group=%s count=%s best=%s x=%s y=%s best_score=%.9f threshold=%.6f elapsed_ms=%.2f",
                    group.name,
                    len(matches),
                    best.image_path,
                    best.x,
                    best.y,
                    best.score,
                    group.threshold,
                    elapsed_ms,
                )
            else:
                self.logger.debug(
                    "findpic_all group=%s count=%s no_match threshold=%.6f elapsed_ms=%.2f",
                    group.name,
                    len(matches),
                    group.threshold,
                    elapsed_ms,
                )
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
        color_tolerance: int = 18,
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
        color_tolerance: int = 18,
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
        color_tolerance: int = 18,
        allowed_bad_pixels: int = 2,
        start: float | None = None,
    ) -> list[MatchResult]:
        start = time.perf_counter() if start is None else start
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
        if limit > 0:
            matches = matches[:limit]

        if self.logger:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if best is not None:
                self.logger.debug(
                    "findpic_pixel_all group=%s count=%s best=%s x=%s y=%s best_score=%.9f threshold=%.6f color_tolerance=%s allowed_bad_pixels=%s elapsed_ms=%.2f",
                    group.name,
                    len(matches),
                    best.image_path,
                    best.x,
                    best.y,
                    best.score,
                    group.threshold,
                    color_tolerance,
                    allowed_bad_pixels,
                    elapsed_ms,
                )
            else:
                self.logger.debug(
                    "findpic_pixel_all group=%s count=%s no_match threshold=%.6f color_tolerance=%s allowed_bad_pixels=%s elapsed_ms=%.2f",
                    group.name,
                    len(matches),
                    group.threshold,
                    color_tolerance,
                    allowed_bad_pixels,
                    elapsed_ms,
                )
        return matches

    def match_pixel_groups(
        self,
        groups: Iterable[ImageGroup],
        region: Region,
        *,
        limit: int = 200,
        color_tolerance: int = 18,
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
            template = cv2.cvtColor(raw_template, cv2.COLOR_GRAY2BGR)
        elif raw_template.shape[2] == 4:
            template = raw_template[:, :, :3]
            alpha = raw_template[:, :, 3]
            if np.any(alpha < 255):
                mask = (alpha > 0).astype(np.uint8) * 255
        else:
            template = raw_template[:, :, :3]

        self._cache[image_path] = (template, mask)
        return template, mask

    def _match_one(
        self,
        *,
        haystack: object,
        template: object,
        mask: object | None,
        region: Region,
        group: str,
        image_path: Path,
    ) -> MatchResult | None:
        import cv2
        import numpy as np

        template_height, template_width = template.shape[:2]
        haystack_height, haystack_width = haystack.shape[:2]
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
            return None

        if mask is not None:
            matched = cv2.matchTemplate(haystack, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        else:
            matched = cv2.matchTemplate(haystack, template, cv2.TM_SQDIFF_NORMED)
        matched = np.nan_to_num(matched, nan=1.0, posinf=1.0, neginf=1.0)
        min_value, _, min_location, _ = cv2.minMaxLoc(matched)
        score = 1.0 - float(min_value)

        return MatchResult(
            group=group,
            image_path=image_path,
            x=region.x + int(min_location[0]),
            y=region.y + int(min_location[1]),
            width=int(template_width),
            height=int(template_height),
            score=score,
        )

    def _match_many(
        self,
        *,
        haystack: object,
        template: object,
        mask: object | None,
        region: Region,
        group: str,
        image_path: Path,
        threshold: float,
    ) -> list[MatchResult]:
        matches, _ = self._match_many_with_best(
            haystack=haystack,
            template=template,
            mask=mask,
            region=region,
            group=group,
            image_path=image_path,
            threshold=threshold,
        )
        return matches

    def _match_many_with_best(
        self,
        *,
        haystack: object,
        template: object,
        mask: object | None,
        region: Region,
        group: str,
        image_path: Path,
        threshold: float,
    ) -> tuple[list[MatchResult], MatchResult | None]:
        import cv2
        import numpy as np

        template_height, template_width = template.shape[:2]
        haystack_height, haystack_width = haystack.shape[:2]
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

        if mask is not None:
            matched = cv2.matchTemplate(haystack, template, cv2.TM_SQDIFF_NORMED, mask=mask)
        else:
            matched = cv2.matchTemplate(haystack, template, cv2.TM_SQDIFF_NORMED)
        matched = np.nan_to_num(matched, nan=1.0, posinf=1.0, neginf=1.0)
        min_value, _, min_location, _ = cv2.minMaxLoc(matched)
        best = MatchResult(
            group=group,
            image_path=image_path,
            x=region.x + int(min_location[0]),
            y=region.y + int(min_location[1]),
            width=int(template_width),
            height=int(template_height),
            score=1.0 - float(min_value),
        )

        accepted = (matched <= (1.0 - threshold + 1e-9)).astype(np.uint8)
        if not np.any(accepted):
            return [], best

        count, labels = cv2.connectedComponents(accepted, connectivity=8)
        results: list[MatchResult] = []
        for label in range(1, count):
            component_mask = (labels == label).astype(np.uint8)
            min_value, _, min_location, _ = cv2.minMaxLoc(matched, mask=component_mask)
            results.append(
                MatchResult(
                    group=group,
                    image_path=image_path,
                    x=region.x + int(min_location[0]),
                    y=region.y + int(min_location[1]),
                    width=int(template_width),
                    height=int(template_height),
                    score=1.0 - float(min_value),
                )
            )
        return results, best

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
        import cv2
        import numpy as np

        template_height, template_width = template.shape[:2]
        haystack_height, haystack_width = haystack.shape[:2]
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
            valid_mask = mask > 0
        valid_count = int(np.count_nonzero(valid_mask))
        if valid_count <= 0:
            return [], None

        windows = np.lib.stride_tricks.sliding_window_view(
            haystack,
            (template_height, template_width),
            axis=(0, 1),
        )
        windows = np.moveaxis(windows, 2, -1)
        diff = np.abs(windows.astype(np.int16) - template.astype(np.int16))
        pixel_ok = np.all(diff <= max(0, int(color_tolerance)), axis=-1)
        good_counts = pixel_ok[..., valid_mask].sum(axis=-1)
        misses = valid_count - good_counts
        scores = good_counts.astype(np.float32) / float(valid_count)

        best_y, best_x = (int(item) for item in np.unravel_index(np.argmax(scores), scores.shape))
        best = MatchResult(
            group=group,
            image_path=image_path,
            x=region.x + best_x,
            y=region.y + best_y,
            width=int(template_width),
            height=int(template_height),
            score=float(scores[best_y, best_x]),
        )

        accepted = (
            (scores >= threshold - 1e-9)
            | (misses <= max(0, int(allowed_bad_pixels)))
        ).astype(np.uint8)
        if not np.any(accepted):
            return [], best

        count, labels = cv2.connectedComponents(accepted, connectivity=8)
        results: list[MatchResult] = []
        for label in range(1, count):
            component_mask = (labels == label).astype(np.uint8)
            _, max_value, _, max_location = cv2.minMaxLoc(scores, mask=component_mask)
            results.append(
                MatchResult(
                    group=group,
                    image_path=image_path,
                    x=region.x + int(max_location[0]),
                    y=region.y + int(max_location[1]),
                    width=int(template_width),
                    height=int(template_height),
                    score=float(max_value),
                )
            )
        return results, best
