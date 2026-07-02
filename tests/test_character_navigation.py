from __future__ import annotations

import logging
import unittest

from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import MoveResult, MoveTarget
from mhscript_yjs.characters.navigation import (
    choose_portal_route,
    find_portal_route,
    move_with_portal_navigation,
)
from mhscript_yjs.characters.position import CharacterPosition
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.timing import NullSleeper


class CharacterNavigationTests(unittest.TestCase):
    def test_find_portal_route_matches_aut3_left_to_right(self) -> None:
        route = find_portal_route(
            122,
            _position(-100, 125),
            MoveTarget(100, 80),
        )

        self.assertIsNotNone(route)
        self.assertEqual(route.entrance, (-78, 125))
        self.assertEqual(route.exit, (39, 80))

    def test_choose_portal_route_requires_direct_distance_margin(self) -> None:
        decision = choose_portal_route(
            122,
            _position(-50, 125),
            MoveTarget(23, 111),
        )

        self.assertIsNone(decision)

    def test_move_with_portal_navigation_uses_entrance_then_final_target(self) -> None:
        device = DryRunDevice()
        controller = _PortalProbeController(
            positions=[
                _position(100, 125),
                _position(30, 125),
                _position(-94, 82),
            ],
        )
        target = MoveTarget(-100, 82, x_tolerance=2, y_tolerance=1)

        result, route = move_with_portal_navigation(
            controller=controller,  # type: ignore[arg-type]
            actions=CharacterActions(device, NullSleeper(), logging.getLogger("test.navigation")),
            target=target,
            map_id=122,
            logger=logging.getLogger("test.navigation"),
        )

        self.assertTrue(result.reached)
        self.assertIsNotNone(route)
        self.assertEqual(
            [(target.x, target.y) for target in controller.move_targets],
            [(28, 125), (-100, 82)],
        )
        key_downs = [action.args[0] for action in device.actions if action.name == "key_down"]
        self.assertEqual(key_downs, [keycode("Left")])
        press_keys = [action.args[0] for action in device.actions if action.name == "press_key"]
        self.assertEqual(press_keys, [keycode("Up"), keycode("Up"), keycode("Up")])

    def test_portal_entry_probes_direction_when_inside_move_tolerance(self) -> None:
        device = DryRunDevice()
        controller = _PortalProbeController(
            positions=[
                _position(35, 114),
                _position(35, 114),
                _position(140, 95),
            ],
        )
        target = MoveTarget(143, 105, x_tolerance=2, y_tolerance=1)

        result, route = move_with_portal_navigation(
            controller=controller,  # type: ignore[arg-type]
            actions=CharacterActions(device, NullSleeper(), logging.getLogger("test.navigation")),
            target=target,
            map_id=161,
            logger=logging.getLogger("test.navigation"),
        )

        self.assertTrue(result.reached)
        self.assertIsNotNone(route)
        self.assertEqual(
            [(target.x, target.y) for target in controller.move_targets],
            [(37, 114), (143, 105)],
        )
        key_downs = [action.args[0] for action in device.actions if action.name == "key_down"]
        self.assertEqual(key_downs, [keycode("Right")])
        press_keys = [action.args[0] for action in device.actions if action.name == "press_key"]
        self.assertEqual(press_keys, [keycode("Up"), keycode("Up"), keycode("Up")])


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
        return MoveResult(True, "reached", 1, _position(target.x, target.y))


def _position(x: int, y: int) -> CharacterPosition:
    return CharacterPosition(x=x, y=y, screen_x=0, screen_y=0, anchor_screen_x=0, anchor_screen_y=0)


if __name__ == "__main__":
    unittest.main()
