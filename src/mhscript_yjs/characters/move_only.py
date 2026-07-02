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

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        retry_up = self._upward_retry_level(max_level=1) > 0
        if retry_up or position.y > target.y + 25:
            if retry_up:
                self.logger.info("[MoveUp] 使用失败升级：MoveOnly 改用高段上跳")
            self.actions.hold_random("Alt", 69, 70)
            return self._wait_vertical_settle(
                position,
                target,
                direction="up",
                timeout_ms=1450,
            )
        else:
            self.actions.hold_random("LAlt", 143, 145)
            return self._wait_vertical_settle(
                position,
                target,
                direction="up",
                timeout_ms=1250,
            )
