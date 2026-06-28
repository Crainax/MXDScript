from __future__ import annotations

import time
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
                    "findpic group=%s accepted=%s x=%s y=%s score=%.6f threshold=%.6f elapsed_ms=%.2f",
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
                    "findpic group=%s below_threshold=%s x=%s y=%s score=%.6f threshold=%.6f elapsed_ms=%.2f",
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
        template = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if template is None:
            raise TemplateNotFoundError(f"Template image could not be read: {image_path}")

        mask = None
        corners = (
            template[0, 0],
            template[0, -1],
            template[-1, 0],
            template[-1, -1],
        )
        if all(np.array_equal(corners[0], corner) for corner in corners[1:]):
            transparent = corners[0]
            mask = np.any(template != transparent, axis=2).astype(np.uint8) * 255
            if not np.any(mask):
                mask = None

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
