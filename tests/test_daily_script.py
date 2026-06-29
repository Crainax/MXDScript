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


@dataclass
class SequenceMatcher:
    matches: dict[str, list[bool]]
    calls: list[str] = field(default_factory=list)
    thresholds: list[float] = field(default_factory=list)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.append(group.name)
        self.thresholds.append(group.threshold)
        sequence = self.matches.setdefault(group.name, [])
        matched = sequence.pop(0) if sequence else False
        if not matched:
            return None
        return MatchResult(
            group=group.name,
            image_path=group.paths[0],
            x=100,
            y=200,
            width=40,
            height=20,
            score=1.0,
        )


@dataclass
class CharacterCoordinateMatcher:
    positions: list[tuple[int, int]]
    anchor: tuple[int, int] = (40, 1757)
    calls: list[str] = field(default_factory=list)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.append(group.name)
        if group.name == "Character.MapAnchor":
            x, y = self.anchor
        elif group.name == "Character.Me":
            if not self.positions:
                return None
            x, y = self.positions.pop(0) if len(self.positions) > 1 else self.positions[0]
        else:
            return None
        return MatchResult(
            group=group.name,
            image_path=group.paths[0],
            x=x,
            y=y,
            width=20,
            height=10,
            score=1.0,
        )


@dataclass
class LynnSkillMatcher:
    available: set[str]
    calls: list[str] = field(default_factory=list)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.append(group.name)
        if group.name not in self.available:
            return None
        return MatchResult(
            group=group.name,
            image_path=group.paths[0],
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

    def test_receive_daily_quest_opens_scheduler_clicks_receive_and_closes(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=SequenceMatcher(
                {
                    "SchedulerUI": [False, True, True, False],
                    "ReceiveButton": [False, True],
                    "ReceivedMark": [False, True],
                }
            ),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )
        runner._initialize_window()  # noqa: SLF001

        state = runner._receive_daily_quest()  # noqa: SLF001

        self.assertEqual(state, "accepted")
        self.assertEqual(
            [action.name for action in device.actions],
            [
                "press_key",
                "move_relative",
                "move_to",
                "left_click",
                "move_to",
                "press_key",
                "move_relative",
            ],
        )
        self.assertEqual(device.actions[0].args, (0xDB, 1))

    def test_receive_daily_quest_done_skips_clear_quest(self) -> None:
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=DryRunDevice(),
            matcher=SequenceMatcher(
                {
                    "SchedulerUI": [True, True, False],
                    "ReceiveButton": [False],
                    "ReceivedMark": [False],
                }
            ),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )
        runner._initialize_window()  # noqa: SLF001
        calls: list[str] = []
        original_receive = runner._receive_daily_quest  # noqa: SLF001
        runner._receive_daily_quest = lambda: (calls.append("receive") or original_receive())  # type: ignore[method-assign]  # noqa: SLF001
        runner._run_clear_quest = lambda: calls.append("clear")  # type: ignore[method-assign]  # noqa: SLF001

        runner._run_daily_quest()  # noqa: SLF001

        self.assertEqual(calls, ["receive"])

    def test_clear_quest_uses_aut_options_instead_of_file_configuration(self) -> None:
        options = {f"aut{flag}": False for flag in range(1, 8)}
        options["aut2"] = True
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=FakeMatcher(),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            options=options,
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )
        calls: list[tuple[str, int | None]] = []
        runner.execute_sub = lambda name: calls.append((name, runner.vars.get("mapFlag")))  # type: ignore[method-assign]

        runner._run_clear_quest()  # noqa: SLF001

        self.assertEqual(
            calls,
            [
                ("ClearAUTGeneric", 2),
                ("CloseScheduler", 2),
                ("Home", 2),
            ],
        )
        self.assertEqual([action.name for action in device.actions], ["press_key", "press_key"])

    def test_km_compatibility_apis(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=FakeMatcher(),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            options={"aut7": False},
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        )

        runner._execute_statement("GetFileLine standTime,files/AUT7.txt,questPointer+1")  # noqa: SLF001
        runner._execute_statement("a = GetTimeStamp()")  # noqa: SLF001
        runner._execute_statement("b = GetLED(1)")  # noqa: SLF001
        runner._execute_statement("KeyAllup")  # noqa: SLF001

        self.assertEqual(runner.vars["standTime"], 0)
        self.assertGreater(runner.vars["a"], 0)
        self.assertEqual(runner.vars["b"], 0)
        self.assertEqual([action.name for action in device.actions], ["release_all_keys"])

    def test_lynn_move_subroutine_uses_refactored_character_controller(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=CharacterCoordinateMatcher(
                positions=[
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1862),
                ]
            ),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=30, y=1721, width=1366, height=768),
        )
        runner._initialize_window()  # noqa: SLF001
        runner.vars.update(
            {
                "CurrentJob": runner.vars["JobLynn"],
                "JumpRange": 24,
                "tarX": 31,
                "tarY": 105,
                "tolerance": 2,
                "yTolerance": 1,
            }
        )

        runner.execute_sub("Move")

        actions = [(action.name, action.args) for action in device.actions]
        self.assertIn(("key_down", (0x28,)), actions)
        self.assertIn(("key_down", (32,)), actions)
        self.assertIn(("key_up", (32,)), actions)
        self.assertIn(("key_up", (0x28,)), actions)
        self.assertEqual(runner.vars["intX"], 31)
        self.assertEqual(runner.vars["intY"], 105)

    def test_lynn_stand_spell_uses_refactored_skill_logic(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=LynnSkillMatcher({"Lynn.D"}),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=30, y=1721, width=1366, height=768),
        )
        runner._initialize_window()  # noqa: SLF001
        runner.vars.update(
            {
                "CurrentJob": runner.vars["JobLynn"],
                "JumpRange": 24,
            }
        )

        runner.execute_sub("StandSpell")

        self.assertEqual(
            [(action.name, action.args) for action in device.actions],
            [
                ("press_key", (0x27, 1)),
                ("key_down", (ord("D"),)),
                ("key_up", (ord("D"),)),
            ],
        )

    def test_lara_move_subroutine_uses_refactored_character_controller(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=CharacterCoordinateMatcher(
                positions=[
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1837),
                    (71, 1862),
                ]
            ),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=30, y=1721, width=1366, height=768),
        )
        runner._initialize_window()  # noqa: SLF001
        runner.vars.update(
            {
                "CurrentJob": runner.vars["JobLara"],
                "JumpRange": 24,
                "tarX": 31,
                "tarY": 105,
                "tolerance": 2,
                "yTolerance": 1,
            }
        )

        runner.execute_sub("Move")

        actions = [(action.name, action.args) for action in device.actions]
        self.assertIn(("key_down", (0x28,)), actions)
        self.assertIn(("key_down", (32,)), actions)
        self.assertIn(("key_up", (32,)), actions)
        self.assertIn(("key_up", (0x28,)), actions)
        self.assertEqual(runner.vars["intX"], 31)
        self.assertEqual(runner.vars["intY"], 105)

    def test_lara_stand_spell_uses_refactored_skill_logic(self) -> None:
        device = DryRunDevice()
        runner = DailyRunner(
            config=load_config(load_local=False),
            device=device,
            matcher=LynnSkillMatcher({"Common.4"}),  # type: ignore[arg-type]
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.daily_script"),
            window_info=WindowInfo(hwnd=100, title="MapleStory", x=30, y=1721, width=1366, height=768),
        )
        runner._initialize_window()  # noqa: SLF001
        runner.vars.update(
            {
                "CurrentJob": runner.vars["JobLara"],
                "JumpRange": 24,
            }
        )

        runner.execute_sub("StandSpell")

        self.assertEqual(
            [(action.name, action.args) for action in device.actions],
            [
                ("key_down", (ord("4"),)),
                ("key_up", (ord("4"),)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
