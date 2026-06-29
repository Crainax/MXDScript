from __future__ import annotations

from dataclasses import dataclass

from mhscript_yjs.characters.base import MoveTarget
from mhscript_yjs.characters.controller import CharacterController
from mhscript_yjs.characters.position import CharacterPosition


@dataclass
class MoveOnlyController(CharacterController):
    def stand_attack(self) -> None:
        self.logger.info("[MoveOnly] stand_attack skipped")

    def move_right_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        self.actions.delay_random(15, 25)
        self.actions.key_down("Right")
        try:
            self.actions.delay_random(50, 51)
            self.actions.hold_random("Space", 80, 81)
            self.actions.delay_random(30, 31)
            self.actions.hold_random("Space", 75, 76)
            self.actions.delay_random(85, 86)
        finally:
            self.actions.key_up("Right")
        self.actions.delay_random(41, 42)
        self.actions.delay_random(256, 261)

    def move_left_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        self.actions.delay_random(15, 25)
        self.actions.key_down("Left")
        try:
            self.actions.delay_random(50, 51)
            self.actions.hold_random("Space", 80, 81)
            self.actions.delay_random(30, 31)
            self.actions.hold_random("Space", 75, 76)
            self.actions.delay_random(85, 86)
        finally:
            self.actions.key_up("Left")
        self.actions.delay_random(41, 42)
        self.actions.delay_random(256, 261)

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> None:
        stable = self._stable()
        if stable is None or stable.y != position.y:
            self.logger.info("[MoveOnly] Y 轴未稳定，跳过本次上移")
            return

        if position.y > target.y + 25:
            self.actions.hold_random("Alt", 69, 70)
            self.actions.delay_random(921, 931)
        else:
            self.actions.hold_random("LAlt", 143, 145)
            self.actions.delay_random(769, 777)
