from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mhscript_yjs.characters import CharacterPosition, LaraController, MoveTarget
from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.scripts.leveling.leveling import LevelingRunner
from mhscript_yjs.scripts.tool.rune_solver import RunePressAttempt, RuneSolverConfig
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

    def test_release_rune_logs_and_starts_auto_solver(self) -> None:
        runner = _runner(LevelingMatcher({"解符文.检测符文"}))
        runner._initialize_window()  # noqa: SLF001
        runner.vars["RuneCooldown"] = 0
        calls: list[MatchResult] = []
        runner._solve_rune = lambda rune: calls.append(rune)  # type: ignore[method-assign]

        with self.assertLogs("test.leveling", level="INFO") as logs:
            runner._release_rune_if_ready()  # noqa: SLF001

        self.assertEqual(len(calls), 1)
        self.assertIn("[解符文] 检测到符文", "\n".join(logs.output))

    def test_solve_rune_sets_cooldown_after_verified_success(self) -> None:
        solver = FakeRuneSolver(
            [RunePressAttempt("pressed", 1, "ok", ("up", "down", "left", "right"))]
        )
        runner = _runner(LevelingMatcher(), rune_solver=solver)
        runner._initialize_window()  # noqa: SLF001
        runner.vars["RuneCooldown"] = 0
        moved: list[MatchResult] = []
        runner._move_to_rune_icon = lambda rune: moved.append(rune) or True  # type: ignore[method-assign]
        runner._leave_rune_and_find_remaining = lambda: (True, None)  # type: ignore[method-assign]

        runner._solve_rune(_match())  # noqa: SLF001

        self.assertEqual(len(moved), 1)
        self.assertEqual(solver.attempts, [1])
        self.assertGreater(runner.vars["RuneCooldown"], 0)

    def test_solve_rune_pauses_after_repeated_unrecognized_attempts(self) -> None:
        attempts = [
            RunePressAttempt("unrecognized", index, "low_slot_confidence")
            for index in range(1, 6)
        ]
        solver = FakeRuneSolver(attempts)
        paused: list[str] = []
        runner = _runner(
            LevelingMatcher(),
            rune_solver=solver,
            request_pause=lambda: paused.append("pause"),
        )
        runner._initialize_window()  # noqa: SLF001
        runner.vars["RuneCooldown"] = 0
        runner._move_to_rune_icon = lambda rune: True  # type: ignore[method-assign]
        runner._pause_for_manual_rune = lambda last_attempt: paused.append(last_attempt.reason)  # type: ignore[method-assign]

        runner._solve_rune(_match())  # noqa: SLF001

        self.assertEqual(solver.attempts, [1, 2, 3, 4, 5])
        self.assertEqual(paused, ["low_slot_confidence"])
        self.assertEqual(runner.vars["RuneCooldown"], 0)

    def test_rune_target_uses_coordinate_detector_relative_position(self) -> None:
        runner = _runner(LevelingMatcher())
        target = runner._rune_target_from_matches(  # noqa: SLF001
            _match_at(143, 1866),
            _match_at(37, 1748),
        )

        self.assertEqual((target.x, target.y), (106, 118))
        self.assertTrue(runner._rune_target_in_detection_region(target))  # noqa: SLF001
        self.assertFalse(runner._rune_target_in_detection_region(_screen_like_target()))  # noqa: SLF001

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
        self.assertEqual(
            [action.name for action in device.actions],
            ["press_key", "press_key", "press_key"],
        )
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
    rune_solver: FakeRuneSolver | None = None,
    request_pause=lambda: None,
) -> LevelingRunner:
    return LevelingRunner(
        config=load_config(load_local=False),
        device=device or DryRunDevice(),
        matcher=matcher,  # type: ignore[arg-type]
        sleeper=NullSleeper(),
        logger=logging.getLogger("test.leveling"),
        window_info=WindowInfo(hwnd=100, title="MapleStory", x=10, y=20, width=800, height=600),
        request_pause=request_pause,
        rune_solver=rune_solver,  # type: ignore[arg-type]
    )


class FakeRuneSolver:
    def __init__(self, results: list[RunePressAttempt]) -> None:
        self.results = results
        self.config = RuneSolverConfig(max_attempts=len(results), retry_delay_ms=0)
        self.attempts: list[int] = []

    def trigger_and_press(self, window: WindowInfo, *, attempt: int) -> RunePressAttempt:
        self.attempts.append(attempt)
        return self.results[attempt - 1]


def _match() -> MatchResult:
    return _match_at(120, 180)


def _match_at(x: int, y: int) -> MatchResult:
    return MatchResult(
        group="Leveling.Rune",
        image_path=Path("Rune.bmp"),
        x=x,
        y=y,
        width=20,
        height=10,
        score=1.0,
    )


def _screen_like_target() -> MoveTarget:
    return MoveTarget(x=144, y=1866, x_tolerance=2, y_tolerance=3)


if __name__ == "__main__":
    unittest.main()
