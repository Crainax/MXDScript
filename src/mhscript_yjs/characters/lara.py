from __future__ import annotations

from dataclasses import dataclass

from mhscript_yjs.characters.base import MoveTarget
from mhscript_yjs.characters.controller import CharacterController
from mhscript_yjs.characters.position import CharacterPosition


@dataclass
class LaraController(CharacterController):
    wg_time: float = 0
    seed_time: float = 0
    release_time: float = 0

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
        self.jump_attack()
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
        self.jump_attack()
        self.actions.delay_random(256, 261)

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        retry_up = self._upward_retry_level(max_level=1) > 0
        if retry_up or position.y > target.y + 25:
            if retry_up:
                self.logger.info("[MoveUp] 使用失败升级：Lara 改用高段上跳")
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

    def jump_attack(self) -> None:
        if self._cast_if_available("Lara.G", (r"E:\MHImg\Lara\G.bmp",), "G", 333, 335):
            return
        if self._cast_if_available("Lara.C2", (r"E:\MHImg\Lara\C2.bmp",), "C", 583, 585):
            return
        if self._cast_if_available("Common.H", (r"E:\MHImg\Common\H.bmp",), "H", 783, 785):
            return
        if self._cast_if_available("Common.Y", (r"E:\MHImg\Common\Y.bmp",), "Y", 583, 585):
            return
        self.logger.info("[Lara] 跳跃攻击使用 F 兜底")
        self.actions.hold_random("F", 313, 315)

    def stand_attack(self) -> None:
        now = self.now()
        if self._skill_available("Common.4", (r"E:\MHImg\Common\4.bmp",)) and now - self.wg_time > 58:
            self.logger.info("[Lara] 释放 4 技能")
            self.actions.hold_random("4", 105, 107)
            self.actions.delay_random(899, 909)
            self.wg_time = self.now()
            return

        if self._skill_available("Common.5G", (r"E:\MHImg\Common\5G.bmp",)):
            self.logger.info("[Lara] 释放 5 技能")
            self.actions.hold_random("5", 134, 142)
            self.actions.delay_random(512, 519)
            return

        if now - self.release_time > 10:
            self.logger.info("[Lara] 释放 S 组合")
            self.actions.delay_random(56, 58)
            self.actions.hold_random("Right", 55, 60)
            self.actions.delay_random(32, 33)
            self.actions.delay_random(136, 138)
            self.actions.delay_random(165, 167)
            self.actions.hold_random("S", 119, 121)
            self.actions.delay_random(248, 251)
            self.actions.hold_random("Right", 106, 108)
            self.actions.delay_random(342, 347)
            self.release_time = self.now()
            return

        if self._cast_if_available("Lara.V", (r"E:\MHImg\Lara\V.bmp",), "V", 123, 130, after_delay=(899, 909)):
            return
        if self._cast_if_available("Lara.B", (r"E:\MHImg\Lara\B.bmp",), "B", 105, 107, after_delay=(712, 780)):
            return

        if now - self.seed_time > 18:
            self.logger.info("[Lara] 释放种子瞬移")
            self.actions.delay_random(56, 58)
            self.actions.key_down("Down")
            try:
                self.actions.delay_random(95, 98)
                self.actions.hold_random("`", 251, 259)
                self.actions.delay_random(119, 123)
            finally:
                self.actions.key_up("Down")
            self.actions.delay_random(455, 469)
            self.seed_time = self.now()
            return

        if self._cast_if_available("Common.H", (r"E:\MHImg\Common\H.bmp",), "H", 683, 685):
            return
        self._cast_if_available("Common.Y", (r"E:\MHImg\Common\Y.bmp",), "Y", 583, 585)

    def _cast_if_available(
        self,
        name: str,
        paths: tuple[str, ...],
        key: str,
        min_ms: int,
        max_ms: int,
        *,
        after_delay: tuple[int, int] | None = None,
    ) -> bool:
        if not self._skill_available(name, paths):
            return False
        self.logger.info("[Lara] 释放 %s 技能", key)
        self.actions.hold_random(key, min_ms, max_ms)
        if after_delay is not None:
            self.actions.delay_random(*after_delay)
        return True
