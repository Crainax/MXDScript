from __future__ import annotations

import time
from dataclasses import dataclass
from logging import Logger

from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import MoveResult, MoveTarget
from mhscript_yjs.characters.position import CharacterPosition, ImageMatchFn, PositionTracker
from mhscript_yjs.runtime.logging import log_important


@dataclass
class CharacterController:
    tracker: PositionTracker
    actions: CharacterActions
    match_image: ImageMatchFn
    logger: Logger
    jump_range: int = 24
    max_move_attempts: int = 80
    static_position: CharacterPosition | None = None
    static_count: int = 0
    _up_retry_level: int = 0

    def reset_map_state(self) -> None:
        self.static_position = None
        self.static_count = 0
        self._up_retry_level = 0

    def locate(self, *, recover: bool = True, use_cache: bool = True) -> CharacterPosition | None:
        return self.tracker.locate(recover=recover, use_cache=use_cache)

    def move_to(self, target: MoveTarget) -> MoveResult:
        log_important(
            self.logger,
            "[Move] 移动到 (%s,%s)，容差 X=%s Y=%s",
            target.x,
            target.y,
            target.x_tolerance,
            target.y_tolerance,
        )
        self._up_retry_level = 0
        last_position: CharacterPosition | None = None
        for attempt in range(1, self.max_move_attempts + 1):
            position = self.locate(recover=True)
            if position is None:
                self.actions.delay(50)
                continue

            last_position = position
            self._anti_jam(position)
            dx = target.x - position.x
            dy = target.y - position.y
            tol_x = max(target.x_tolerance, 2)
            self.logger.info(
                "[Move] 第 %s 次 当前=(%s,%s) 目标=(%s,%s) dx=%s dy=%s",
                attempt,
                position.x,
                position.y,
                target.x,
                target.y,
                dx,
                dy,
            )

            if position.x < target.x - self.jump_range:
                self.logger.info("[Move] 动作=向右长距离移动")
                self.move_right_long(position, target)
            elif position.x > target.x + self.jump_range:
                self.logger.info("[Move] 动作=向左长距离移动")
                self.move_left_long(position, target)
            elif position.x < target.x - tol_x:
                duration = _clamp((target.x - tol_x - position.x) * 52, 52, 1800)
                self.logger.info("[Move] 动作=向右微调 duration_ms=%s", duration)
                self.actions.hold("Right", duration)
            elif position.x > target.x + tol_x:
                duration = _clamp((position.x - target.x - tol_x) * 52, 52, 1800)
                self.logger.info("[Move] 动作=向左微调 duration_ms=%s", duration)
                self.actions.hold("Left", duration)
            elif position.y < target.y - target.y_tolerance:
                self.logger.info("[Move] 动作=向下移动")
                self._up_retry_level = 0
                moved_position = self.move_down(position, target)
                if moved_position is not None:
                    last_position = moved_position
            elif position.y > target.y + target.y_tolerance:
                self.logger.info("[Move] 动作=向上移动")
                moved_position = self.move_up(position, target)
                if moved_position is not None:
                    last_position = moved_position
                    self._record_up_movement(position, moved_position, target)
            else:
                self._up_retry_level = 0
                log_important(
                    self.logger,
                    "[Move] 已到达 (%s,%s)，当前=(%s,%s)，尝试次数=%s",
                    target.x,
                    target.y,
                    position.x,
                    position.y,
                    attempt,
                )
                return MoveResult(True, "reached", attempt, position)

        self.logger.warning(
            "[Move] 未能到达 (%s,%s)，最后坐标=%s，尝试次数=%s",
            target.x,
            target.y,
            last_position,
            self.max_move_attempts,
        )
        return MoveResult(False, "attempt_limit", self.max_move_attempts, last_position)

    def stand_attack(self) -> None:
        raise NotImplementedError

    def move_right_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        raise NotImplementedError

    def move_left_long(self, position: CharacterPosition, target: MoveTarget) -> None:
        raise NotImplementedError

    def move_up(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        raise NotImplementedError

    def move_down(self, position: CharacterPosition, target: MoveTarget) -> CharacterPosition | None:
        self.logger.info("[MoveDown] Down+Space 下跳，当前Y=%s 目标Y=%s", position.y, target.y)
        self.actions.key_down("Down")
        space_down = False
        try:
            self.actions.delay_random(134, 136)
            self.actions.key_down("Space")
            space_down = True
            if position.y > target.y - 13:
                self.actions.delay_random(162, 164)
            else:
                self.actions.delay((target.y - position.y) * 49 - 520)
        finally:
            if space_down:
                self.actions.key_up("Space")
                self.actions.delay_random(66, 67)
            self.actions.key_up("Down")
        return self._wait_vertical_settle(
            position,
            target,
            direction="down",
            timeout_ms=self._down_settle_timeout_ms(position, target),
        )

    def wait_stable_big(self, current_y: int | None = None) -> bool:
        position_y = current_y
        if position_y is None:
            position = self.locate(recover=True)
            if position is None:
                return False
            position_y = position.y
        return self._stable_big(position_y)

    def _stable(self) -> CharacterPosition | None:
        while True:
            first = self.locate(recover=True)
            if first is None:
                return None
            stable_count = 1
            last = first
            for _ in range(4):
                current = self.locate(recover=True)
                if current is None:
                    return None
                last = current
                if current.y == first.y:
                    self.actions.delay(2)
                    stable_count += 1
                else:
                    break
            if stable_count >= 4:
                return last

    def _stable_big(self, current_y: int) -> bool:
        for _ in range(8):
            current = self.locate(recover=True)
            if current is None:
                return False
            if current.y != current_y:
                return False
            self.actions.delay(2)
        return True

    def _wait_vertical_settle(
        self,
        start: CharacterPosition,
        target: MoveTarget,
        *,
        direction: str,
        timeout_ms: int,
        poll_ms: int = 35,
        stable_samples: int = 3,
        unchanged_grace_ms: int = 260,
    ) -> CharacterPosition | None:
        started_at = self.now()
        deadline = started_at + max(1, timeout_ms) / 1000
        last_y: int | None = None
        stable_count = 0
        moved = False
        rebound = False
        best = start

        while self.now() < deadline:
            self.actions.delay(poll_ms)
            current = self.locate(recover=False, use_cache=False)
            if current is None:
                continue

            best = current
            elapsed_ms = int((self.now() - started_at) * 1000)
            if direction == "up":
                if current.y < start.y:
                    moved = True
                if moved and last_y is not None and current.y > last_y:
                    rebound = True
            else:
                if current.y > start.y:
                    moved = True

            if current.y == last_y:
                stable_count += 1
            else:
                last_y = current.y
                stable_count = 1

            if stable_count < stable_samples:
                continue
            if not moved:
                if elapsed_ms >= unchanged_grace_ms:
                    self.logger.info(
                        "[MoveVertical] no_y_change direction=%s y=%s elapsed_ms=%s",
                        direction,
                        current.y,
                        elapsed_ms,
                    )
                    return current
                continue
            if direction == "up" and self._upward_peak_without_landing(current, target, rebound):
                continue

            self.logger.info(
                "[MoveVertical] settled direction=%s start_y=%s current_y=%s target_y=%s elapsed_ms=%s",
                direction,
                start.y,
                current.y,
                target.y,
                elapsed_ms,
            )
            return current

        self.logger.info(
            "[MoveVertical] timeout direction=%s start_y=%s best_y=%s target_y=%s timeout_ms=%s",
            direction,
            start.y,
            best.y,
            target.y,
            timeout_ms,
        )
        return best

    def _upward_peak_without_landing(
        self,
        current: CharacterPosition,
        target: MoveTarget,
        rebound: bool,
    ) -> bool:
        tolerance = max(target.y_tolerance, 0)
        if rebound:
            return False
        if abs(current.y - target.y) <= tolerance:
            return False
        return current.y < target.y - tolerance

    def _down_settle_timeout_ms(self, position: CharacterPosition, target: MoveTarget) -> int:
        distance = max(0, target.y - position.y)
        return _clamp(distance * 45 + 500, 800, 1800)

    def _upward_retry_level(self, *, max_level: int = 1) -> int:
        return min(max(0, self._up_retry_level), max(0, max_level))

    def _record_up_movement(
        self,
        start: CharacterPosition,
        current: CharacterPosition,
        target: MoveTarget,
    ) -> None:
        still_needs_up = current.y > target.y + target.y_tolerance
        progress = start.y - current.y
        min_progress = max(2, target.y_tolerance + 1)
        if still_needs_up and progress < min_progress:
            self._up_retry_level += 1
            self.logger.info(
                "[MoveUp] 上跳未有效抬升 start_y=%s current_y=%s target_y=%s retry_level=%s",
                start.y,
                current.y,
                target.y,
                self._up_retry_level,
            )
            return
        if self._up_retry_level:
            self.logger.info("[MoveUp] 上跳已产生有效抬升，清除失败升级")
        self._up_retry_level = 0

    def _anti_jam(self, position: CharacterPosition) -> None:
        if self.static_position and self.static_position.x == position.x and self.static_position.y == position.y:
            self.static_count += 1
        else:
            self.static_position = position
            self.static_count = 0
        if self.static_count < 5:
            return
        self.logger.warning("[Move] 检测到疑似卡住，执行右跳脱困")
        self.actions.release_all()
        self.actions.delay_random(13, 15)
        self.actions.key_down("Right")
        try:
            self.actions.delay_random(21, 22)
            self.actions.hold_random("Space", 76, 77)
            self.actions.delay_random(44, 45)
        finally:
            self.actions.key_up("Right")
        self.actions.delay_random(13, 15)
        self.static_count = 0

    def _skill_available(self, name: str, paths: tuple[str, ...]) -> bool:
        match = self.match_image(name, paths, self.tracker.skill_region(), 1.0)
        available = match is not None
        self.logger.info("[Skill] %s available=%s", name, available)
        return available

    @staticmethod
    def now() -> float:
        return time.monotonic()


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
