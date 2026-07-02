from __future__ import annotations

import logging
import os
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mhscript_yjs.characters import CharacterPosition, LaraController, MoveTarget
from mhscript_yjs.characters.base import Job, MoveResult
from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.scripts.leveling.leveling import (
    LevelingRunner,
    read_leveling_potion_payload,
    reset_leveling_potion_timer,
)
from mhscript_yjs.scripts.tool.rune_solver import RunePressAttempt, RuneSolverConfig
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo


@dataclass
class LevelingMatcher:
    enabled_groups: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    groups: dict[str, ImageGroup] = field(default_factory=dict)

    def match_any(self, group: ImageGroup, region: Region) -> MatchResult | None:
        self.calls.append(group.name)
        self.groups[group.name] = group
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

    def test_map_detection_uses_leveling_map_templates(self) -> None:
        matcher = LevelingMatcher()
        runner = _runner(matcher)
        runner._initialize_window()  # noqa: SLF001

        runner._match_map("AUT1")  # noqa: SLF001

        group = matcher.groups["Leveling.Map.AUT1"]
        paths = {str(path).replace("/", "\\") for path in group.paths}
        self.assertTrue(any(path.endswith(r"Maps\AUT1.bmp") for path in paths))
        self.assertFalse(any(path.endswith(r"UI\F2\Map\AUT1.bmp") for path in paths))

    def test_potion_confirmation_matches_all_relative_confirm_templates(self) -> None:
        matcher = LevelingMatcher()
        runner = _runner(matcher)
        runner._initialize_window()  # noqa: SLF001

        runner._confirm_potion_dialog_if_present()  # noqa: SLF001

        group = matcher.groups["Leveling.PotionDialog"]
        paths = {str(path).replace("/", "\\") for path in group.paths}
        self.assertTrue(any(path.endswith(r"UI\OK.bmp") for path in paths))
        self.assertTrue(any(path.endswith(r"UI\OK2.bmp") for path in paths))
        self.assertTrue(any(path.endswith(r"UI\Potion_Confirm.bmp") for path in paths))

    def test_map_detection_skips_unused_leveling_branches(self) -> None:
        matcher = LevelingMatcher({"Leveling.Map.AUT5"})
        runner = _runner(matcher)
        runner._initialize_window()  # noqa: SLF001

        map_id = runner._confirm_aut_map()  # noqa: SLF001

        self.assertEqual(map_id, 0)
        self.assertNotIn("Leveling.Map.AUT5", matcher.calls)

    def test_unknown_aut3_and_aut4_submaps_remain_unconfigured(self) -> None:
        runner = _runner(LevelingMatcher())
        runner._teleport_position = lambda: None  # type: ignore[method-assign]

        self.assertEqual(runner._confirm_aut3_submap(), 0)  # noqa: SLF001
        self.assertEqual(runner._confirm_aut4_submap(), 0)  # noqa: SLF001

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

    def test_aut3_left_to_right_navigation_uses_shared_navi(self) -> None:
        device = DryRunDevice()
        runner = _runner(LevelingMatcher(), device=device)
        runner.vars["map"] = 122
        controller = _PortalProbeController(
            positions=[
                CharacterPosition(100, 125, 0, 0, 0, 0),
                CharacterPosition(30, 125, 0, 0, 0, 0),
                CharacterPosition(-94, 82, 0, 0, 0, 0),
            ]
        )
        runner._active_character_controller = lambda: controller  # type: ignore[method-assign]

        runner._aut_navi(-100, 82, tolerance=4, y_tolerance=0)  # noqa: SLF001

        move_targets = [
            (target.x, target.y, target.x_tolerance, target.y_tolerance)
            for target in controller.move_targets
        ]
        self.assertEqual(
            move_targets,
            [(28, 125, 2, 0), (-100, 82, 4, 0)],
        )
        key_downs = [int(action.args[0]) for action in device.actions if action.name == "key_down"]
        self.assertEqual(key_downs, [keycode("Left")])
        press_keys = [
            int(action.args[0])
            for action in device.actions
            if action.name == "press_key"
        ]
        self.assertEqual(press_keys, [keycode("Up"), keycode("Up"), keycode("Up")])

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

    def test_auto_potion_uses_top_row_2_confirms_dialog_and_persists_by_job(self) -> None:
        with _temporary_local_appdata():
            device = DryRunDevice()
            runner = _runner(LevelingMatcher({"Leveling.PotionDialog"}), device=device)
            runner._initialize_window()  # noqa: SLF001
            runner.vars["CurrentJob"] = runner.vars["JobLynn"]
            runner._wall_clock = lambda: 10_000.0  # type: ignore[method-assign]

            runner._use_potion_if_ready()  # noqa: SLF001

            pressed_codes = [
                int(action.args[0])
                for action in device.actions
                if action.name == "press_key"
            ]
            self.assertEqual(pressed_codes[:2], [keycode("p"), keycode("2")])
            self.assertIn(keycode("enter"), pressed_codes)
            self.assertEqual(
                read_leveling_potion_payload(Job.LYNN, now=10_000.0)["potionMinutesSinceLastUse"],
                0,
            )
            self.assertIsNone(
                read_leveling_potion_payload(Job.LARA, now=10_000.0)["potionLastUsedAt"]
            )

    def test_auto_potion_waits_30_minutes_per_job(self) -> None:
        with _temporary_local_appdata():
            device = DryRunDevice()
            runner = _runner(LevelingMatcher(), device=device)
            runner._initialize_window()  # noqa: SLF001
            runner.vars["CurrentJob"] = runner.vars["JobLynn"]
            clock = 20_000.0
            runner._wall_clock = lambda: clock  # type: ignore[method-assign]

            runner._use_potion_if_ready()  # noqa: SLF001
            first_action_count = len(device.actions)
            clock += 29 * 60
            runner._wall_clock = lambda: clock  # type: ignore[method-assign]
            runner._use_potion_if_ready()  # noqa: SLF001

            self.assertEqual(len(device.actions), first_action_count)

            clock += 60
            runner._wall_clock = lambda: clock  # type: ignore[method-assign]
            runner._use_potion_if_ready()  # noqa: SLF001
            self.assertGreater(len(device.actions), first_action_count)

            lara = _runner(LevelingMatcher(), device=DryRunDevice())
            lara._initialize_window()  # noqa: SLF001
            lara.vars["CurrentJob"] = lara.vars["JobLara"]
            lara._wall_clock = lambda: 20_100.0  # type: ignore[method-assign]
            lara._use_potion_if_ready()  # noqa: SLF001
            self.assertEqual(
                read_leveling_potion_payload(Job.LARA, now=20_100.0)["potionMinutesSinceLastUse"],
                0,
            )

    def test_auto_potion_only_triggers_in_attack_zone(self) -> None:
        with _temporary_local_appdata():
            device = DryRunDevice()
            runner = _runner(LevelingMatcher(), device=device)
            runner._initialize_window()  # noqa: SLF001
            now = runner._get_timestamp()  # noqa: SLF001
            runner.vars.update(
                {
                    "CurrentJob": runner.vars["JobLynn"],
                    "map": 101,
                    "fountainTime": now,
                    "yanusTime": now,
                    "patrolTime": now,
                    "Key7Time": now,
                }
            )
            runner._wall_clock = lambda: 30_000.0  # type: ignore[method-assign]
            runner._aut_navi = lambda *args, **kwargs: None  # type: ignore[method-assign]
            runner._wiggle = lambda attack_facing: None  # type: ignore[method-assign]
            runner._active_character_controller = lambda: _FakeController()  # type: ignore[method-assign]
            runner._locate_character = lambda: CharacterPosition(0, 90, 0, 0, 0, 0)  # type: ignore[method-assign]

            runner._process_unified_map()  # noqa: SLF001

            pressed_codes = [
                int(action.args[0])
                for action in device.actions
                if action.name == "press_key"
            ]
            self.assertNotIn(keycode("p"), pressed_codes)

            device.actions.clear()
            runner._locate_character = lambda: CharacterPosition(-40, 103, 0, 0, 0, 0)  # type: ignore[method-assign]
            runner._process_unified_map()  # noqa: SLF001
            pressed_codes = [
                int(action.args[0])
                for action in device.actions
                if action.name == "press_key"
            ]
            self.assertEqual(pressed_codes[:2], [keycode("p"), keycode("2")])

    def test_clear_potion_timer_makes_current_job_due_immediately(self) -> None:
        with _temporary_local_appdata():
            payload = reset_leveling_potion_timer(Job.LYNN, now=50_000.0)

            self.assertEqual(payload["potionJob"], "lynn")
            self.assertEqual(payload["potionMinutesSinceLastUse"], 100)
            self.assertEqual(
                read_leveling_potion_payload(Job.LYNN, now=50_000.0)["potionMinutesSinceLastUse"],
                100,
            )

    def test_auto_potion_option_is_checked_live_while_running(self) -> None:
        with _temporary_local_appdata():
            device = DryRunDevice()
            enabled = False
            runner = _runner(
                LevelingMatcher(),
                device=device,
                options_provider=lambda: {"autoPotion": enabled},
            )
            runner._initialize_window()  # noqa: SLF001
            runner.vars["CurrentJob"] = runner.vars["JobLynn"]
            runner._wall_clock = lambda: 60_000.0  # type: ignore[method-assign]

            runner._use_potion_if_ready()  # noqa: SLF001

            self.assertEqual(device.actions, [])

            enabled = True
            runner._use_potion_if_ready()  # noqa: SLF001
            pressed_codes = [
                int(action.args[0])
                for action in device.actions
                if action.name == "press_key"
            ]
            self.assertEqual(pressed_codes[:2], [keycode("p"), keycode("2")])


class _FakeController:
    def stand_attack(self) -> None:
        return None


class _PortalProbeController:
    def __init__(self, positions: list[CharacterPosition]) -> None:
        self.positions = positions
        self.move_targets: list[MoveTarget] = []

    def locate(self, *, recover: bool = True, use_cache: bool = True) -> CharacterPosition | None:
        if not self.positions:
            return None
        return self.positions.pop(0)

    def move_to(self, target: MoveTarget) -> MoveResult:
        self.move_targets.append(target)
        return MoveResult(True, "reached", 1, CharacterPosition(target.x, target.y, 0, 0, 0, 0))


def _runner(
    matcher: LevelingMatcher,
    *,
    device: DryRunDevice | None = None,
    rune_solver: FakeRuneSolver | None = None,
    request_pause=lambda: None,
    emit_data=lambda payload: None,
    options_provider=None,
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
        emit_data=emit_data,
        options_provider=options_provider,
    )


class _temporary_local_appdata:
    def __init__(self) -> None:
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        self._old_value: str | None = None

    def __enter__(self) -> Path:
        self._tempdir = tempfile.TemporaryDirectory()
        self._old_value = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._tempdir.name
        return Path(self._tempdir.name)

    def __exit__(self, *args: object) -> None:
        if self._old_value is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old_value
        if self._tempdir is not None:
            self._tempdir.cleanup()


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
