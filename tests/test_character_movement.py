from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass, field

from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import MoveTarget
from mhscript_yjs.characters.controller import CharacterController
from mhscript_yjs.characters.lara import LaraController
from mhscript_yjs.characters.lynn import LynnController
from mhscript_yjs.characters.position import CharacterPosition, PositionTracker
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.timing import NullSleeper
from mhscript_yjs.vision.types import MatchResult
from mhscript_yjs.windows.maple import WindowInfo


class CharacterMovementTests(unittest.TestCase):
    def test_upward_wait_ignores_stable_jump_peak_until_rebound(self) -> None:
        start = _position(100)
        tracker = _SequenceTracker(
            [
                _position(70),
                _position(70),
                _position(70),
                _position(75),
                _position(80),
                _position(80),
                _position(80),
            ]
        )
        controller = _controller(tracker)

        result = controller._wait_vertical_settle(  # noqa: SLF001
            start,
            MoveTarget(0, 80),
            direction="up",
            timeout_ms=1000,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.y, 80)
        self.assertTrue(all(call == (False, False) for call in tracker.locate_calls))

    def test_move_down_uses_observed_settle_instead_of_legacy_tail_sleep(self) -> None:
        start = _position(80)
        tracker = _SequenceTracker(
            [
                _position(86),
                _position(96),
                _position(96),
                _position(96),
            ]
        )
        sleeper = _RecordingSleeper()
        controller = _controller(tracker, sleeper=sleeper)

        result = controller.move_down(start, MoveTarget(0, 96))

        self.assertIsNotNone(result)
        self.assertEqual(result.y, 96)
        self.assertNotIn((548, 554), sleeper.random_ranges)
        self.assertIn((134, 136), sleeper.random_ranges)
        self.assertIn((66, 67), sleeper.random_ranges)
        self.assertTrue(all(call == (False, False) for call in tracker.locate_calls))

    def test_repeated_short_down_steps_detect_rope_and_hold_down(self) -> None:
        device = DryRunDevice()
        tracker = _SequenceTracker(
            [
                _position_xy(161, 105),
                _position_xy(161, 108),
                _position_xy(161, 108),
                _position_xy(161, 108),
                _position_xy(161, 108),
                _position_xy(161, 112),
                _position_xy(161, 113),
                _position_xy(161, 113),
                _position_xy(161, 113),
                _position_xy(161, 114),
                _position_xy(161, 114),
                _position_xy(161, 114),
                _position_xy(161, 114),
            ]
        )
        controller = _controller(tracker, device=device)

        with self.assertLogs("test.character", level="INFO") as logs:
            result = controller.move_to(MoveTarget(161, 114, x_tolerance=2, y_tolerance=1))

        self.assertTrue(result.reached)
        key_downs = _key_downs(device)
        self.assertGreaterEqual(key_downs.count(keycode("Down")), 3)
        self.assertNotIn(keycode("Right"), key_downs)
        log_text = "\n".join(logs.output)
        self.assertIn("疑似绳子", log_text)
        self.assertIn("长按 Down", log_text)

    def test_live_locate_reuses_cached_anchor_and_only_matches_character(self) -> None:
        calls: list[str] = []

        def match_image(name, _paths, _region, _threshold):
            calls.append(name)
            if name == "Character.Me":
                return _match(name, 120, 140)
            if name == "Character.MapAnchor":
                return _match(name, 100, 120)
            return None

        tracker = PositionTracker(
            window=WindowInfo(hwnd=1, title="MapleStory", x=0, y=0, width=800, height=600),
            match_image=match_image,
            device=DryRunDevice(),
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.character"),
        )

        first = tracker.locate()
        calls.clear()
        second = tracker.locate(recover=False, use_cache=False)

        self.assertEqual(first, _position_at(20, 20, 120, 140, 100, 120))
        self.assertEqual(second, _position_at(20, 20, 120, 140, 100, 120))
        self.assertEqual(calls, ["Character.Me"])

    def test_live_poll_miss_logs_info_instead_of_warning(self) -> None:
        calls: list[str] = []

        def match_image(name, _paths, _region, _threshold):
            calls.append(name)
            if name == "Character.Me" and calls.count("Character.Me") == 1:
                return _match(name, 120, 140)
            if name == "Character.MapAnchor":
                return _match(name, 100, 120)
            return None

        logger_name = "test.character.live_poll_miss"
        tracker = PositionTracker(
            window=WindowInfo(hwnd=1, title="MapleStory", x=0, y=0, width=800, height=600),
            match_image=match_image,
            device=DryRunDevice(),
            sleeper=NullSleeper(),
            logger=logging.getLogger(logger_name),
        )

        self.assertIsNotNone(tracker.locate())
        with self.assertLogs(logger_name, level="INFO") as logs:
            self.assertIsNone(tracker.locate(recover=False, use_cache=False))

        self.assertTrue(
            any("INFO:" in line and "实时定位本帧未命中" in line for line in logs.output)
        )
        self.assertFalse(any(line.startswith("WARNING:") for line in logs.output))

    def test_normal_locate_failure_still_logs_warning(self) -> None:
        logger_name = "test.character.locate_failure"
        tracker = PositionTracker(
            window=WindowInfo(hwnd=1, title="MapleStory", x=0, y=0, width=800, height=600),
            match_image=lambda *_args: None,
            device=DryRunDevice(),
            sleeper=NullSleeper(),
            logger=logging.getLogger(logger_name),
        )

        with self.assertLogs(logger_name, level="WARNING") as logs:
            self.assertIsNone(tracker.locate(recover=False, use_cache=True))

        self.assertTrue(any("定位失败 me=no anchor=no" in line for line in logs.output))

    def test_recover_locate_failure_does_not_move_mouse(self) -> None:
        device = DryRunDevice()
        tracker = PositionTracker(
            window=WindowInfo(hwnd=1, title="MapleStory", x=0, y=0, width=800, height=600),
            match_image=lambda *_args: None,
            device=device,
            sleeper=NullSleeper(),
            logger=logging.getLogger("test.character.locate_no_mouse"),
        )

        self.assertIsNone(tracker.locate(recover=True, use_cache=False))

        self.assertNotIn("move_to", [action.name for action in device.actions])

    def test_failed_upward_jump_marks_next_attempt_for_upgrade(self) -> None:
        tracker = _SequenceTracker([_position(104), _position(104), _position(82)])
        controller = _retry_controller(
            tracker,
            up_results=[_position(104), _position(82)],
        )

        result = controller.move_to(MoveTarget(0, 82))

        self.assertTrue(result.reached)
        self.assertEqual(controller.retry_levels, [0, 1])
        self.assertEqual(controller._up_retry_level, 0)  # noqa: SLF001

    def test_effective_upward_progress_keeps_fast_branch(self) -> None:
        tracker = _SequenceTracker([_position(114), _position(104), _position(82)])
        controller = _retry_controller(
            tracker,
            up_results=[_position(104), _position(82)],
        )

        result = controller.move_to(MoveTarget(0, 82))

        self.assertTrue(result.reached)
        self.assertEqual(controller.retry_levels, [0, 0])

    def test_lynn_failed_small_up_retry_uses_lalt_branch(self) -> None:
        device = DryRunDevice()
        controller = _lynn_controller(
            _SequenceTracker([_position(82), _position(82), _position(82)]),
            device=device,
        )
        controller._up_retry_level = 1  # noqa: SLF001

        controller.move_up(_position(104), MoveTarget(0, 82))

        key_downs = _key_downs(device)
        self.assertEqual(key_downs[0], keycode("LAlt"))
        self.assertNotIn(keycode("Up"), key_downs)

    def test_lara_failed_small_up_retry_uses_high_jump_branch(self) -> None:
        device = DryRunDevice()
        controller = _lara_controller(
            _SequenceTracker([_position(82), _position(82), _position(82)]),
            device=device,
        )
        controller._up_retry_level = 1  # noqa: SLF001

        controller.move_up(_position(104), MoveTarget(0, 82))

        key_downs = _key_downs(device)
        self.assertEqual(key_downs[0], keycode("Alt"))
        self.assertNotIn(keycode("LAlt"), key_downs)


class _ProbeController(CharacterController):
    def stand_attack(self) -> None:
        return None

    def move_right_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        return None

    def move_left_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        return None

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        return self._wait_vertical_settle(position, target, direction="up", timeout_ms=1000)


@dataclass
class _RetryProbeController(_ProbeController):
    up_results: list[CharacterPosition] = field(default_factory=list)
    retry_levels: list[int] = field(default_factory=list)

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        self.retry_levels.append(self._upward_retry_level(max_level=1))  # noqa: SLF001
        return self.up_results.pop(0)


@dataclass
class _SequenceTracker:
    positions: list[CharacterPosition | None]
    locate_calls: list[tuple[bool, bool]] = field(default_factory=list)
    _index: int = 0

    def locate(self, *, recover: bool = True, use_cache: bool = True) -> CharacterPosition | None:
        self.locate_calls.append((recover, use_cache))
        if self._index >= len(self.positions):
            return self.positions[-1]
        position = self.positions[self._index]
        self._index += 1
        return position


class _RecordingSleeper(NullSleeper):
    def __init__(self) -> None:
        super().__init__()
        self.delays: list[int] = []
        self.random_ranges: list[tuple[int, int]] = []

    def delay_ms(self, milliseconds: int) -> None:
        self.delays.append(int(milliseconds))
        super().delay_ms(milliseconds)

    def delay_random_ms(self, min_ms: int, max_ms: int) -> int:
        self.random_ranges.append((int(min_ms), int(max_ms)))
        return super().delay_random_ms(min_ms, max_ms)


def _controller(
    tracker: _SequenceTracker,
    *,
    sleeper: NullSleeper | None = None,
    device: DryRunDevice | None = None,
) -> _ProbeController:
    sleeper = sleeper or _RecordingSleeper()
    device = device or DryRunDevice()
    return _ProbeController(
        tracker=tracker,  # type: ignore[arg-type]
        actions=CharacterActions(device, sleeper, logging.getLogger("test.character")),
        match_image=lambda *_args, **_kwargs: None,
        logger=logging.getLogger("test.character"),
    )


def _retry_controller(
    tracker: _SequenceTracker,
    *,
    up_results: list[CharacterPosition],
) -> _RetryProbeController:
    return _RetryProbeController(
        tracker=tracker,  # type: ignore[arg-type]
        actions=CharacterActions(DryRunDevice(), _RecordingSleeper(), logging.getLogger("test.character")),
        match_image=lambda *_args, **_kwargs: None,
        logger=logging.getLogger("test.character"),
        up_results=up_results,
    )


def _lynn_controller(tracker: _SequenceTracker, *, device: DryRunDevice) -> LynnController:
    return LynnController(
        tracker=tracker,  # type: ignore[arg-type]
        actions=CharacterActions(device, NullSleeper(), logging.getLogger("test.character")),
        match_image=lambda *_args, **_kwargs: None,
        logger=logging.getLogger("test.character"),
    )


def _lara_controller(tracker: _SequenceTracker, *, device: DryRunDevice) -> LaraController:
    return LaraController(
        tracker=tracker,  # type: ignore[arg-type]
        actions=CharacterActions(device, NullSleeper(), logging.getLogger("test.character")),
        match_image=lambda *_args, **_kwargs: None,
        logger=logging.getLogger("test.character"),
    )


def _key_downs(device: DryRunDevice) -> list[int]:
    return [int(action.args[0]) for action in device.actions if action.name == "key_down"]


def _position(y: int) -> CharacterPosition:
    return CharacterPosition(x=0, y=y, screen_x=0, screen_y=y, anchor_screen_x=0, anchor_screen_y=0)


def _position_xy(x: int, y: int) -> CharacterPosition:
    return CharacterPosition(x=x, y=y, screen_x=x, screen_y=y, anchor_screen_x=0, anchor_screen_y=0)


def _position_at(
    x: int,
    y: int,
    screen_x: int,
    screen_y: int,
    anchor_x: int,
    anchor_y: int,
) -> CharacterPosition:
    return CharacterPosition(
        x=x,
        y=y,
        screen_x=screen_x,
        screen_y=screen_y,
        anchor_screen_x=anchor_x,
        anchor_screen_y=anchor_y,
    )


def _match(group: str, x: int, y: int) -> MatchResult:
    return MatchResult(
        group=group,
        image_path=group,
        x=x,
        y=y,
        width=1,
        height=1,
        score=1.0,
    )


if __name__ == "__main__":
    unittest.main()
