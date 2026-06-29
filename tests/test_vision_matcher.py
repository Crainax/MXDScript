from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from mhscript_yjs.vision.matcher import TemplateMatcher
from mhscript_yjs.vision.types import ImageGroup, Region


class FakeCapture:
    def __init__(self, image: np.ndarray) -> None:
        self.image = image
        self.calls = 0

    def capture_region(self, region: Region) -> np.ndarray:
        self.calls += 1
        return self.image


class VisionMatcherTests(unittest.TestCase):
    def test_bmp_corners_are_not_treated_as_transparent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "template.bmp"
            template = np.full((3, 3, 3), (0, 0, 255), dtype=np.uint8)
            template[1, 1] = (0, 255, 0)
            cv2.imwrite(str(template_path), template)

            haystack = np.zeros((8, 8, 3), dtype=np.uint8)
            haystack[4, 4] = (0, 255, 0)
            matcher = TemplateMatcher(FakeCapture(haystack), logger=logging.getLogger("test.matcher"))

            match = matcher.match_any(
                ImageGroup("bmp", (template_path,), 0.99),
                Region(x=0, y=0, width=8, height=8),
            )

        self.assertIsNone(match)

    def test_png_alpha_is_used_as_transparency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "template.png"
            template = np.zeros((3, 3, 4), dtype=np.uint8)
            template[1, 1] = (0, 255, 0, 255)
            template[1, 2] = (255, 0, 0, 255)
            cv2.imwrite(str(template_path), template)

            haystack = np.zeros((8, 8, 3), dtype=np.uint8)
            haystack[4, 4] = (0, 255, 0)
            haystack[4, 5] = (255, 0, 0)
            matcher = TemplateMatcher(FakeCapture(haystack), logger=logging.getLogger("test.matcher"))

            match = matcher.match_any(
                ImageGroup("png", (template_path,), 0.99),
                Region(x=0, y=0, width=8, height=8),
            )

        self.assertIsNotNone(match)
        self.assertEqual((match.x, match.y), (3, 3))

    def test_match_all_reports_multiple_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "template.bmp"
            template = np.array(
                [
                    [(10, 20, 30), (20, 30, 40)],
                    [(30, 40, 50), (40, 50, 60)],
                ],
                dtype=np.uint8,
            )
            cv2.imwrite(str(template_path), template)

            haystack = np.zeros((8, 8, 3), dtype=np.uint8)
            haystack[1:3, 1:3] = template
            haystack[5:7, 4:6] = template
            matcher = TemplateMatcher(FakeCapture(haystack), logger=logging.getLogger("test.matcher"))

            matches = matcher.match_all(
                ImageGroup("multi", (template_path,), 0.99),
                Region(x=0, y=0, width=8, height=8),
            )

        self.assertEqual(sorted((match.x, match.y) for match in matches), [(1, 1), (4, 5)])

    def test_match_any_returns_match_without_logger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "template.bmp"
            template = np.array(
                [
                    [(10, 20, 30), (20, 30, 40)],
                    [(30, 40, 50), (40, 50, 60)],
                ],
                dtype=np.uint8,
            )
            cv2.imwrite(str(template_path), template)

            haystack = np.zeros((8, 8, 3), dtype=np.uint8)
            haystack[2:4, 3:5] = template
            matcher = TemplateMatcher(FakeCapture(haystack), logger=None)

            match = matcher.match_any(
                ImageGroup("without_logger", (template_path,), 0.99),
                Region(x=0, y=0, width=8, height=8),
            )

        self.assertIsNotNone(match)
        self.assertEqual((match.x, match.y), (3, 2))

    def test_match_groups_captures_once_for_multiple_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_a_path = Path(directory) / "template_a.bmp"
            template_b_path = Path(directory) / "template_b.bmp"
            template_a = np.array(
                [
                    [(10, 20, 30), (20, 30, 40)],
                    [(30, 40, 50), (40, 50, 60)],
                ],
                dtype=np.uint8,
            )
            template_b = np.array(
                [
                    [(70, 80, 90), (80, 90, 100)],
                    [(90, 100, 110), (100, 110, 120)],
                ],
                dtype=np.uint8,
            )
            cv2.imwrite(str(template_a_path), template_a)
            cv2.imwrite(str(template_b_path), template_b)

            haystack = np.zeros((10, 10, 3), dtype=np.uint8)
            haystack[1:3, 2:4] = template_a
            haystack[5:7, 6:8] = template_b
            capture = FakeCapture(haystack)
            matcher = TemplateMatcher(capture, logger=logging.getLogger("test.matcher"))

            matches = matcher.match_groups(
                (
                    ImageGroup("a", (template_a_path,), 0.99),
                    ImageGroup("b", (template_b_path,), 0.99),
                ),
                Region(x=0, y=0, width=10, height=10),
            )

        self.assertEqual(capture.calls, 1)
        self.assertEqual([(match.x, match.y) for match in matches["a"]], [(2, 1)])
        self.assertEqual([(match.x, match.y) for match in matches["b"]], [(6, 5)])

    def test_pixel_match_all_allows_color_tolerance_and_bad_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_path = Path(directory) / "template.bmp"
            template = np.array(
                [
                    [(10, 20, 30), (20, 30, 40), (30, 40, 50)],
                    [(40, 50, 60), (50, 60, 70), (60, 70, 80)],
                    [(70, 80, 90), (80, 90, 100), (90, 100, 110)],
                ],
                dtype=np.uint8,
            )
            cv2.imwrite(str(template_path), template)

            haystack = np.zeros((8, 8, 3), dtype=np.uint8)
            noisy = template.copy()
            noisy[0, 0] = (200, 200, 200)
            noisy[1, 1] = noisy[1, 1] + 8
            haystack[3:6, 2:5] = noisy
            matcher = TemplateMatcher(FakeCapture(haystack), logger=logging.getLogger("test.matcher"))

            matches = matcher.match_pixel_all(
                ImageGroup("pixel", (template_path,), 1.0),
                Region(x=0, y=0, width=8, height=8),
                color_tolerance=10,
                allowed_bad_pixels=1,
            )

        self.assertEqual([(match.x, match.y) for match in matches], [(2, 3)])
        self.assertAlmostEqual(matches[0].score, 8 / 9)

    def test_pixel_match_groups_captures_once_for_multiple_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template_a_path = Path(directory) / "pixel_a.bmp"
            template_b_path = Path(directory) / "pixel_b.bmp"
            template_a = np.full((2, 2, 3), (10, 20, 30), dtype=np.uint8)
            template_b = np.full((2, 2, 3), (70, 80, 90), dtype=np.uint8)
            cv2.imwrite(str(template_a_path), template_a)
            cv2.imwrite(str(template_b_path), template_b)

            haystack = np.zeros((8, 8, 3), dtype=np.uint8)
            haystack[1:3, 2:4] = template_a
            haystack[5:7, 4:6] = template_b
            capture = FakeCapture(haystack)
            matcher = TemplateMatcher(capture, logger=logging.getLogger("test.matcher"))

            matches = matcher.match_pixel_groups(
                (
                    ImageGroup("a", (template_a_path,), 1.0),
                    ImageGroup("b", (template_b_path,), 1.0),
                ),
                Region(x=0, y=0, width=8, height=8),
                color_tolerance=0,
                allowed_bad_pixels=0,
            )

        self.assertEqual(capture.calls, 1)
        self.assertEqual([(match.x, match.y) for match in matches["a"]], [(2, 1)])
        self.assertEqual([(match.x, match.y) for match in matches["b"]], [(4, 5)])


if __name__ == "__main__":
    unittest.main()
