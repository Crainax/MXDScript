from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mhscript_yjs.characters import CharacterPosition, LaraController
from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.scripts.leveling.leveling import LevelingRunner
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo


@dataclass
class LevelingMatcher:
    enabled_groups: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.append(group.name)
        if group.name in self.enabled_groups or self._matches_lara_feature(group):
            return MatchResult(
                group=group.name,
                image_path=group.paths[0] if group.paths else Path("fake.bmp"),
                x=100,
                y=200,
                width=20,
                height=10,
                score=1.0,
            )
        return None

    @staticmethod
    def _matches_lara_feature(group: ImageGroup) -> bool:
        return any("\\Lara\\Feature.bmp" in str(path) for path in group.paths)


class LevelingScriptTests(unittest.TestCase):
    def test_uses_shared_job_initialization_and_lara_controller(self) -> None:
        runner = _runner(LevelingMatcher())

        runner._initialize_window()  # noqa: SLF001
        runner._initialize_job()  # noqa: SLF001

        self.assertEqual(runner.vars["CurrentJob"], runner.vars["JobLara"])
        self.assertIsInstance(runner._active_character_controller(), LaraController)  # noqa: SLF001

    def test_release_rune_logs_and_delegates_to_reused_release_rune_sub(self) -> None:
        runner = _runner(LevelingMatcher({"Leveling.Rune"}))
        runner._initialize_window()  # noqa: SLF001
        runner.vars["RuneCooldown"] = 0
        calls: list[str] = []
        runner.execute_sub = lambda name: calls.append(name)  # type: ignore[method-assign]

        with self.assertLogs("test.leveling", level="INFO") as logs:
            runner._release_rune_if_ready()  # noqa: SLF001

        self.assertEqual(calls, ["ReleaseRune"])
        self.assertIn("前往解轮", "\n".join(logs.output))

    def test_reincarnation_stone_uses_character_stability_instead_of_km_stablebig(self) -> None:
        device = DryRunDevice()
        runner = _runner(LevelingMatcher(), device=device)
        runner.vars["stoneTime"] = 0
        stable_calls: list[str] = []
        sub_calls: list[str] = []
        runner._wait_stable_big = lambda: stable_calls.append("stable")  # type: ignore[method-assign]
        runner.execute_sub = lambda name: sub_calls.append(name)  # type: ignore[method-assign]

        runner._place_reincarnation_stone_if_ready()  # noqa: SLF001

        self.assertEqual(stable_calls, ["stable"])
        self.assertEqual(sub_calls, [])
        self.assertEqual([action.name for action in device.actions], ["key_down", "key_up"] * 4)

    def test_process_ball_logic_logs_yanus_ball_indices(self) -> None:
        device = DryRunDevice()
        runner = _runner(LevelingMatcher(), device=device)
        runner.vars["map"] = 122
        moves: list[tuple[int, int, int]] = []
        runner._aut_navi = lambda x, y, *, tolerance, y_tolerance=0: moves.append((x, y, tolerance))  # type: ignore[method-assign]

        with self.assertLogs("test.leveling", level="INFO") as logs:
            runner._process_ball_logic(runner._get_map_config())  # noqa: SLF001

        self.assertEqual(moves, [(-81, 97, 2), (-81, 125, 2), (39, 80, 2)])
        self.assertEqual([action.name for action in device.actions], ["press_key", "press_key", "press_key"])
        log_text = "\n".join(logs.output)
        self.assertIn("前往放亚努斯(球),第1个", log_text)
        self.assertIn("前往放亚努斯(球),第2个", log_text)
        self.assertIn("前往放亚努斯(球),第3个", log_text)

    def test_process_unified_map_logs_attack_return_wiggle_and_key7(self) -> None:
        device = DryRunDevice()
        runner = _runner(LevelingMatcher(), device=device)
        runner._initialize_window()  # noqa: SLF001
        now = runner._get_timestamp()  # noqa: SLF001
        runner.vars.update(
            {
                "map": 101,
                "fountainTime": now,
                "yanusTime": now,
                "patrolTime": now,
                "Key7Time": 0,
            }
        )
        runner._locate_character = lambda: CharacterPosition(0, 90, 0, 0, 0, 0)  # type: ignore[method-assign]
        runner._aut_navi = lambda *args, **kwargs: None  # type: ignore[method-assign]
        runner._wiggle = lambda attack_facing: None  # type: ignore[method-assign]
        runner._active_character_controller = lambda: _FakeController()  # type: ignore[method-assign]

        with self.assertLogs("test.leveling", level="INFO") as logs:
            runner._process_unified_map()  # noqa: SLF001

        log_text = "\n".join(logs.output)
        self.assertIn("回到攻击点", log_text)
        self.assertIn("按7键吃里程道具", log_text)
        self.assertEqual([action.name for action in device.actions], ["press_key"])

    def test_process_unified_map_logs_anti_jam_wiggle(self) -> None:
        runner = _runner(LevelingMatcher())
        runner._initialize_window()  # noqa: SLF001
        now = runner._get_timestamp()  # noqa: SLF001
        runner.vars.update(
            {
                "map": 101,
                "fountainTime": now,
                "yanusTime": now,
                "patrolTime": 0,
                "Key7Time": now,
            }
        )
        runner._locate_character = lambda: CharacterPosition(-40, 103, 0, 0, 0, 0)  # type: ignore[method-assign]
        runner._wiggle = lambda attack_facing: None  # type: ignore[method-assign]

        with self.assertLogs("test.leveling", level="INFO") as logs:
            runner._process_unified_map()  # noqa: SLF001

        self.assertIn("左右晃防呆", "\n".join(logs.output))


class _FakeController:
    def stand_attack(self) -> None:
        return None


def _runner(
    matcher: LevelingMatcher,
    *,
    device: DryRunDevice | None = None,
) -> LevelingRunner:
    return LevelingRunner(
        config=load_config(load_local=False),
        device=device or DryRunDevice(),
        matcher=matcher,  # type: ignore[arg-type]
        sleeper=NullSleeper(),
        logger=logging.getLogger("test.leveling"),
        window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
    )


if __name__ == "__main__":
    unittest.main()
