from __future__ import annotations

import unittest
from pathlib import Path

from mhscript_yjs.scripts.tool.coordinate_mover import _coerce_move_mode, _detect_navi_map
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region


class CoordinateMoverTests(unittest.TestCase):
    def test_coerce_move_mode_accepts_navi(self) -> None:
        self.assertEqual(_coerce_move_mode("Navi"), "Navi")
        self.assertEqual(_coerce_move_mode("move"), "Move")
        self.assertEqual(_coerce_move_mode("unknown"), "MoveB")

    def test_detect_navi_map_uses_aut3_teleport_submap(self) -> None:
        runner = _NaviMapRunner(
            enabled_groups={"CoordinateMover.Map.AUT3"},
            teleport=(95, 124),
            anchor=(200, 0),
        )

        map_id = _detect_navi_map(runner)

        self.assertEqual(map_id, 122)
        group = runner.groups["CoordinateMover.Map.AUT3"]
        paths = {str(path).replace("/", "\\") for path in group.paths}
        self.assertTrue(any(path.endswith(r"Maps\AUT3.bmp") for path in paths))
        self.assertFalse(any(path.endswith(r"UI\F2\Map\AUT3.bmp") for path in paths))


class _NaviMapRunner:
    def __init__(
        self,
        *,
        enabled_groups: set[str],
        teleport: tuple[int, int],
        anchor: tuple[int, int],
    ) -> None:
        self.enabled_groups = enabled_groups
        self.teleport = teleport
        self.anchor = anchor
        self.groups: dict[str, ImageGroup] = {}

    def _region(self, _left: str, _top: str, _right: str, _bottom: str) -> Region:
        return Region.from_bounds(0, 0, 400, 330)

    def _match_optional(
        self,
        name: str,
        raw_paths: tuple[str, ...],
        _region: Region,
    ) -> MatchResult | None:
        paths = tuple(Path(path) for path in raw_paths)
        self.groups[name] = ImageGroup(name=name, paths=paths, threshold=1.0)
        if name == "CoordinateMover.MapAnchor":
            return _match(name, self.anchor[0], self.anchor[1], paths[0])
        if name == "CoordinateMover.Teleport":
            return _match(name, self.teleport[0], self.teleport[1], paths[0])
        if name in self.enabled_groups:
            return _match(name, 10, 10, paths[0])
        return None


def _match(name: str, x: int, y: int, path: Path) -> MatchResult:
    return MatchResult(
        group=name,
        image_path=path,
        x=x,
        y=y,
        width=1,
        height=1,
        score=1.0,
    )


if __name__ == "__main__":
    unittest.main()
