from __future__ import annotations

import time
from dataclasses import dataclass
from logging import Logger

from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import MoveResult, MoveTarget
from mhscript_yjs.characters.position import CharacterPosition, ImageMatchFn, PositionTracker
from mhscript_yjs.runtime.logging import log_important


@dataclass
class VerticalSettleResult:
    position: CharacterPosition | None
    elapsed_ms: int
    moved: bool


@dataclass
class DownMoveObservation:
    start: CharacterPosition
    end: CharacterPosition
    elapsed_ms: int
    suspected_rope: bool

    @property
    def delta_x(self) -> int:
        return self.end.x - self.start.x

    @property
    def delta_y(self) -> int:
        return self.end.y - self.start.y


@dataclass
class CharacterController:
    tracker: PositionTracker
    actions: CharacterActions
    match_image: ImageMatchFn
    logger: Logger
    jump_range: int = 24
    max_move_attempts: int = 80
    _up_retry_level: int = 0
    _down_rope_streak: int = 0
    _last_down_observation: DownMoveObservation | None = None

    def reset_map_state(self) -> None:
        self._up_retry_level = 0
        self._clear_down_rope_state()

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
                self._clear_down_rope_state()
                self.logger.info("[Move] 动作=向右长距离移动")
                self.move_right_long(position, target)
            elif position.x > target.x + self.jump_range:
                self._clear_down_rope_state()
                self.logger.info("[Move] 动作=向左长距离移动")
                self.move_left_long(position, target)
            elif position.x < target.x - tol_x:
                self._clear_down_rope_state()
                duration = _clamp((target.x - tol_x - position.x) * 52, 52, 1800)
                self.logger.info("[Move] 动作=向右微调 duration_ms=%s", duration)
                self.actions.hold("Right", duration)
            elif position.x > target.x + tol_x:
                self._clear_down_rope_state()
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
                self._clear_down_rope_state()
                moved_position = self.move_up(position, target)
                if moved_position is not None:
                    last_position = moved_position
                    self._record_up_movement(position, moved_position, target)
            else:
                if self._last_down_observation is not None and self._last_down_observation.suspected_rope:
                    recovered_position = self._recover_from_rope(
                        position,
                        target,
                        reason="within_tolerance_after_short_down_steps",
                    )
                    if recovered_position is not None:
                        last_position = recovered_position
                    continue
                self._up_retry_level = 0
                self._clear_down_rope_state()
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
        settle = self._wait_vertical_settle_result(
            position,
            target,
            direction="down",
            timeout_ms=self._down_settle_timeout_ms(position, target),
        )
        moved_position = settle.position
        if moved_position is not None:
            self._record_down_movement(position, moved_position, target, settle)
            if self._down_rope_streak >= 2:
                recovered_position = self._recover_from_rope(
                    moved_position,
                    target,
                    reason="repeated_short_down_steps",
                )
                if recovered_position is not None:
                    return recovered_position
        return moved_position

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
        return self._wait_vertical_settle_result(
            start,
            target,
            direction=direction,
            timeout_ms=timeout_ms,
            poll_ms=poll_ms,
            stable_samples=stable_samples,
            unchanged_grace_ms=unchanged_grace_ms,
        ).position

    def _wait_vertical_settle_result(
        self,
        start: CharacterPosition,
        target: MoveTarget,
        *,
        direction: str,
        timeout_ms: int,
        poll_ms: int = 35,
        stable_samples: int = 3,
        unchanged_grace_ms: int = 260,
    ) -> VerticalSettleResult:
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
                    return VerticalSettleResult(current, elapsed_ms, moved)
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
            return VerticalSettleResult(current, elapsed_ms, moved)

        elapsed_ms = int((self.now() - started_at) * 1000)
        self.logger.info(
            "[MoveVertical] timeout direction=%s start_y=%s best_y=%s target_y=%s timeout_ms=%s",
            direction,
            start.y,
            best.y,
            target.y,
            timeout_ms,
        )
        return VerticalSettleResult(best, elapsed_ms, moved)

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

    def _record_down_movement(
        self,
        start: CharacterPosition,
        current: CharacterPosition,
        target: MoveTarget,
        settle: VerticalSettleResult,
    ) -> None:
        suspected_rope = self._is_suspected_rope_down_step(start, current, settle)
        observation = DownMoveObservation(
            start=start,
            end=current,
            elapsed_ms=settle.elapsed_ms,
            suspected_rope=suspected_rope,
        )
        self._last_down_observation = observation
        if suspected_rope:
            self._down_rope_streak += 1
            log_important(
                self.logger,
                "[MoveDown] 检测到疑似绳子下爬：start=(%s,%s) current=(%s,%s) "
                "delta=(%s,%s) elapsed_ms=%s streak=%s target=(%s,%s) toleranceY=%s",
                start.x,
                start.y,
                current.x,
                current.y,
                observation.delta_x,
                observation.delta_y,
                settle.elapsed_ms,
                self._down_rope_streak,
                target.x,
                target.y,
                target.y_tolerance,
            )
            return
        if self._down_rope_streak:
            self.logger.info("[MoveDown] 向下移动恢复正常，清除疑似绳子计数")
        self._clear_down_rope_state()

    def _is_suspected_rope_down_step(
        self,
        start: CharacterPosition,
        current: CharacterPosition,
        settle: VerticalSettleResult,
    ) -> bool:
        delta_y = current.y - start.y
        if not settle.moved or delta_y <= 0:
            return False
        if abs(current.x - start.x) > 1:
            return False
        if delta_y > 8:
            return False
        return settle.elapsed_ms <= 700

    def _recover_from_rope(
        self,
        position: CharacterPosition,
        target: MoveTarget,
        *,
        reason: str,
    ) -> CharacterPosition | None:
        observation = self._last_down_observation
        if observation is None:
            return None
        log_important(
            self.logger,
            "[MoveDown] 疑似处于绳子上，改用长按 Down 脱离：reason=%s current=(%s,%s) "
            "target=(%s,%s) last_delta=(%s,%s) streak=%s",
            reason,
            position.x,
            position.y,
            target.x,
            target.y,
            observation.delta_x,
            observation.delta_y,
            self._down_rope_streak,
        )
        self.actions.release_all()
        self.actions.delay_random(13, 15)
        self.actions.key_down("Down")
        try:
            self.actions.delay_random(650, 800)
        finally:
            self.actions.key_up("Down")
        recovered = self._wait_vertical_settle(
            position,
            target,
            direction="down",
            timeout_ms=1400,
            stable_samples=3,
            unchanged_grace_ms=350,
        )
        if recovered is not None:
            log_important(
                self.logger,
                "[MoveDown] 绳子脱离后重新定位：current=(%s,%s) target=(%s,%s)",
                recovered.x,
                recovered.y,
                target.x,
                target.y,
            )
        self._clear_down_rope_state()
        return recovered

    def _clear_down_rope_state(self) -> None:
        self._down_rope_streak = 0
        self._last_down_observation = None

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
