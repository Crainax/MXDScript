from __future__ import annotations

from dataclasses import dataclass

from mhscript_yjs.characters.base import MoveTarget
from mhscript_yjs.characters.controller import CharacterController
from mhscript_yjs.characters.position import CharacterPosition


@dataclass
class LynnController(CharacterController):
    spell_ok_time: float = 0
    fire_time: float = 0
    wg_time: float = 0

    def reset_map_state(self) -> None:
        super().reset_map_state()
        self.fire_time = 0

    def move_right_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        if position.x < target.x - 44:
            self.actions.delay_random(15, 25)
            self.actions.key_down("Right")
            try:
                self.actions.delay(37)
                self.actions.hold("Space", 54)
                self.actions.delay(54)
                self.actions.hold("Space", 78)
                self.actions.delay(59)
                self.actions.hold("F", 89)
                self.actions.delay(226)
                self.actions.hold("LShift", 79)
                self.actions.delay(236)
            finally:
                self.actions.key_up("Right")
            self.actions.delay(109)
            self.actions.key_down("F")
            try:
                self.actions.delay(13)
                self.actions.hold("G", 62)
                self.actions.delay(12)
            finally:
                self.actions.key_up("F")
            self.actions.delay(346)
            return

        self.actions.delay_random(15, 25)
        self.actions.key_down("Right")
        try:
            self.actions.delay(50)
            self.actions.hold("Space", 87)
            self.actions.delay(46)
            self.actions.key_down("Space")
            self.actions.delay(104)
        finally:
            self.actions.key_up("Right")
            self.actions.delay(1)
            self.actions.key_up("Space")
        self.actions.delay(131)
        self.actions.hold("F", 103)
        self.actions.delay(325)

    def move_left_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        if position.x > target.x + 44:
            self.actions.delay_random(15, 25)
            self.actions.key_down("Left")
            try:
                self.actions.delay(37)
                self.actions.hold("Space", 54)
                self.actions.delay(54)
                self.actions.hold("Space", 78)
                self.actions.delay(59)
                self.actions.hold("F", 89)
                self.actions.delay(226)
                self.actions.hold("LShift", 79)
                self.actions.delay(236)
            finally:
                self.actions.key_up("Left")
            self.actions.delay(109)
            self.actions.key_down("F")
            try:
                self.actions.delay(13)
                self.actions.hold("G", 62)
                self.actions.delay(12)
            finally:
                self.actions.key_up("F")
            self.actions.delay(346)
            return

        self.actions.delay_random(15, 25)
        self.actions.key_down("Left")
        try:
            self.actions.delay(50)
            self.actions.hold("Space", 87)
            self.actions.delay(46)
            self.actions.key_down("Space")
            self.actions.delay(104)
        finally:
            self.actions.key_up("Left")
            self.actions.delay(1)
            self.actions.key_up("Space")
        self.actions.delay(131)
        self.actions.hold("F", 103)
        self.actions.delay(325)

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        retry_up = self._upward_retry_level(max_level=1) > 0
        if position.y > target.y + 40:
            self.actions.delay_random(15, 25)
            self.actions.key_down("Up")
            try:
                self.actions.delay(80)
                self.actions.hold("Space", 52)
                self.actions.delay(73)
                self.actions.hold("Space", 92)
                self.actions.delay(69)
            finally:
                self.actions.key_up("Up")
            self.actions.delay(52)
            self.actions.delay(104)
            self.actions.delay(401)
            self.actions.hold("LAlt", 89)
            return self._wait_vertical_settle(
                position,
                target,
                direction="up",
                timeout_ms=1900,
            )
        elif retry_up or position.y > target.y + 32:
            if retry_up:
                self.logger.info("[MoveUp] 使用失败升级：Lynn 改用 LAlt 上跳")
            self.actions.hold_random("LAlt", 69, 70)
            return self._wait_vertical_settle(
                position,
                target,
                direction="up",
                timeout_ms=1450,
            )
        else:
            self.actions.delay_random(78, 79)
            self.actions.key_down("Up")
            try:
                self.actions.delay_random(105, 107)
                self.actions.hold_random("Space", 78, 79)
                self.actions.delay_random(94, 95)
                self.actions.hold_random("Space", 96, 97)
                self.actions.delay_random(76, 77)
            finally:
                self.actions.key_up("Up")
            self.actions.delay_random(160, 162)
            self.actions.delay_random(95, 96)
            return self._wait_vertical_settle(
                position,
                target,
                direction="up",
                timeout_ms=1300,
            )

    def stand_attack(self) -> None:
        now = self.now()
        if self._skill_available("Lynn.D", (r"E:\MHImg\Lynn\D.bmp",)) and now - self.fire_time > 27:
            self.logger.info("[Lynn] 释放 D 技能")
            self.actions.press("Right")
            self.actions.delay(68)
            self.actions.key_down("D")
            self.fire_time = self.now()
            self.actions.delay(71)
            self.actions.key_up("D")
            self.actions.delay(476)
            return

        if self._skill_available("Common.4", (r"E:\MHImg\Common\4.bmp",)) and now - self.wg_time > 55:
            self.logger.info("[Lynn] 释放 4 技能")
            self.actions.delay(100)
            self.actions.hold_random("4", 505, 507)
            self.actions.delay_random(209, 299)
            self.wg_time = self.now()
            return

        if self._skill_available("Common.5A", (r"E:\MHImg\Common\5A.bmp",)):
            self.logger.info("[Lynn] 释放 5 技能")
            self.actions.delay(100)
            self.actions.hold_random("5", 134, 142)
            self.actions.delay_random(512, 519)
            return

        if now - self.spell_ok_time <= 8:
            self._fallback_attack()
            return

        if self._cast_if_available("Lynn.B", (r"E:\MHImg\Lynn\B.bmp",), "B", 522, 535):
            return
        if self._cast_if_available("Lynn.S", (r"E:\MHImg\Lynn\S.bmp",), "S", 462, 465):
            return
        if self._cast_if_available("Lynn.G", (r"E:\MHImg\Lynn\G.bmp", r"E:\MHImg\Lynn\G2.bmp"), "G", 562, 565):
            return
        if self._skill_available("Lynn.V", (r"E:\MHImg\Lynn\V.bmp",)):
            self.logger.info("[Lynn] 尝试释放 V 技能")
            self.actions.hold_random("V", 532, 535)
            if not self._skill_available("Lynn.V.after", (r"E:\MHImg\Lynn\V.bmp",)):
                self.spell_ok_time = self.now()
            return
        if self._cast_if_available("Common.H", (r"E:\MHImg\Common\H.bmp",), "H", 682, 685):
            return
        if self._cast_if_available("Common.Y", (r"E:\MHImg\Common\Y.bmp",), "Y", 512, 525):
            return
        self._fallback_attack()

    def _cast_if_available(
        self,
        name: str,
        paths: tuple[str, ...],
        key: str,
        min_ms: int,
        max_ms: int,
    ) -> bool:
        if not self._skill_available(name, paths):
            return False
        self.logger.info("[Lynn] 释放 %s 技能", key)
        self.actions.hold_random(key, min_ms, max_ms)
        self.spell_ok_time = self.now()
        return True

    def _fallback_attack(self) -> None:
        self.logger.info("[Lynn] 使用 F 兜底攻击")
        self.actions.delay(68)
        self.actions.hold("F", 2520)
        self.actions.delay(181)
