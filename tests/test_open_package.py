from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.scripts.tool.open_package import OpenPackageRunner
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo


@dataclass
class FakeMatcher:
    matches: dict[str, list[bool]]
    calls: list[str] = field(default_factory=list)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.append(group.name)
        remaining = self.matches.setdefault(group.name, [])
        found = remaining.pop(0) if remaining else False
        if not found:
            return None
        return MatchResult(
            group=group.name,
            image_path=Path(f"{group.name}.bmp"),
            x=100,
            y=200,
            width=20,
            height=10,
            score=1.0,
        )


class OpenPackageTests(unittest.TestCase):
    def test_jing_branch_clicks_and_waits_for_confirm(self) -> None:
        config = load_config(load_local=False)
        matcher = FakeMatcher(
            {
                "confirm": [False, True],
                "jing": [True],
                "shi": [],
            }
        )
        device = DryRunDevice()
        runner = OpenPackageRunner(
            config=config,
            device=device,
            matcher=matcher,  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.open_package"),
            window_info=_window(),
        )

        result = runner.run(max_iterations=1)

        self.assertEqual(result.exit_reason, "iteration_limit")
        self.assertEqual(result.no_find_count, 0)
        self.assertEqual(result.cards_opened, 0)
        self.assertEqual(
            [action.name for action in device.actions],
            ["open", "move_to", "left_click", "press_key", "close"],
        )
        self.assertEqual(device.actions[1].args, (110, 200, True))
        self.assertEqual(device.actions[3].args, (13, 1))
        self.assertEqual(runner.next_after_confirm, 2)

    def test_confirm_branch_waits_for_shi_then_switches_to_jing_next(self) -> None:
        config = load_config(load_local=False)
        matcher = FakeMatcher(
            {
                "confirm": [True],
                "jing": [],
                "shi": [True],
            }
        )
        device = DryRunDevice()
        runner = OpenPackageRunner(
            config=config,
            device=device,
            matcher=matcher,  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.open_package"),
            window_info=_window(),
        )

        result = runner.run(max_iterations=1)

        self.assertEqual(result.exit_reason, "iteration_limit")
        self.assertEqual(result.no_find_count, 0)
        self.assertEqual(
            [action.name for action in device.actions],
            ["open", "press_key", "move_to", "left_click", "close"],
        )
        self.assertEqual(result.cards_opened, 0)
        self.assertEqual(runner.next_after_confirm, 3)

    def test_shi_branch_counts_ten_opened_cards_after_confirm(self) -> None:
        config = load_config(load_local=False)
        matcher = FakeMatcher(
            {
                "confirm": [False, True],
                "jing": [False],
                "shi": [True],
            }
        )
        device = DryRunDevice()
        runner = OpenPackageRunner(
            config=config,
            device=device,
            matcher=matcher,  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.open_package"),
            window_info=_window(),
        )

        result = runner.run(max_iterations=1)

        self.assertEqual(result.exit_reason, "iteration_limit")
        self.assertEqual(result.no_find_count, 0)
        self.assertEqual(result.cards_opened, 10)
        self.assertEqual(runner.next_after_confirm, 3)

    def test_no_match_exits_on_no_find_limit(self) -> None:
        config = load_config(load_local=False)
        matcher = FakeMatcher({"confirm": [], "jing": [], "shi": []})
        device = DryRunDevice()
        runner = OpenPackageRunner(
            config=config,
            device=device,
            matcher=matcher,  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.open_package"),
            window_info=_window(),
        )

        result = runner.run(max_iterations=3)

        self.assertEqual(result.exit_reason, "iteration_limit")
        self.assertEqual(result.no_find_count, 3)
        self.assertEqual(result.cards_opened, 0)
        self.assertEqual(matcher.calls, ["confirm", "jing", "shi"] * 3)


def _window() -> WindowInfo:
    return WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600)


if __name__ == "__main__":
    unittest.main()
