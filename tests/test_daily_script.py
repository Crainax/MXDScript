from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.scripts.daily.combine_main import DailyRunner
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo


@dataclass
class FakeMatcher:
    calls: list[str] = field(default_factory=list)
    thresholds: list[float] = field(default_factory=list)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.extend(str(path) for path in group.paths)
        self.thresholds.append(group.threshold)
        lara_feature = next((path for path in group.paths if "\\Lara\\Feature.bmp" in str(path)), None)
        if lara_feature is None:
            return None
        return MatchResult(
            group=group.name,
            image_path=Path(lara_feature),
            x=100,
            y=200,
            width=20,
            height=10,
            score=1.0,
        )


class DailyScriptTests(unittest.TestCase):
    def test_initializes_job_even_when_all_modules_are_disabled(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=FakeMatcher(),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            options={
                "dailyQuest": False,
                "gugu": False,
                "summerDaily": False,
                "otherDaily": False,
            },
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )

        result = runner.run()

        self.assertEqual(result.exit_reason, "completed")
        self.assertEqual(runner.vars["CurrentJob"], runner.vars["JobLara"])
        self.assertEqual(
            result.modules,
            {
                "dailyQuest": "skipped",
                "gugu": "skipped",
                "summerDaily": "skipped",
                "otherDaily": "skipped",
            },
        )
        self.assertEqual([action.name for action in device.actions], ["open", "close"])
        self.assertEqual(runner.matcher.thresholds, [0.99, 0.99])  # type: ignore[attr-defined]

    def test_configured_match_threshold_relaxes_findpic_after_job_detection(self) -> None:
        matcher = FakeMatcher()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=DryRunDevice(),
            matcher=matcher,  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            options={"matchThreshold": 0.94},
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )

        runner._find_pic(  # noqa: SLF001
            Region.from_bounds(0, 0, 100, 100),
            (r"E:\MHImg\UI\Daily\Gugu\Mark1.bmp",),
            1.0,
            "x",
            "y",
        )

        self.assertEqual(matcher.thresholds[-1], 0.94)

    def test_mouse_api_commands_are_supported(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=FakeMatcher(),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )
        runner.vars.update({"xEnd": 1000, "yEnd": 800})

        runner._execute_statement("MoveR 50,50")  # noqa: SLF001
        runner._execute_statement("MoveD xEnd - 725, yEnd - 613, 0")  # noqa: SLF001
        runner._execute_statement("LeftDown")  # noqa: SLF001
        runner._execute_statement("LeftUp")  # noqa: SLF001
        runner._execute_statement("MouseWheel 1")  # noqa: SLF001

        self.assertEqual(
            [(action.name, action.args) for action in device.actions],
            [
                ("move_relative", (50, 50)),
                ("move_to", (275, 187, True)),
                ("left_down", ()),
                ("left_up", ()),
                ("mouse_wheel", (1,)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
