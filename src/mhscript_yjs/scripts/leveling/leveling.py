from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mhscript_yjs.characters import MoveTarget
from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import Job
from mhscript_yjs.characters.navigation import move_with_portal_navigation
from mhscript_yjs.core.config import ProjectConfig
from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.controlled import ControlledInputDevice
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.drivers.yjs import YjsDevice
from mhscript_yjs.runtime.app_paths import app_data_dir
from mhscript_yjs.runtime.control import RunControl, StopRequested
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.runtime.timing import NullSleeper, Sleeper
from mhscript_yjs.scripts.daily.combine_main import DailyRunner
from mhscript_yjs.scripts.tool.rune_solver import RuneSolver
from mhscript_yjs.vision.matcher import TemplateMatcher
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.windows.maple import WindowInfo

LEVELING_SCRIPT_ID = "leveling"
LEVELING_SCRIPT_NAME = "练级"
SUPPORTED_LEVELING_MAPS = {101, 111, 121, 122, 132, 161, -232}
POTION_INTERVAL_SECONDS = 30 * 60
POTION_INTERVAL_MINUTES = POTION_INTERVAL_SECONDS // 60
POTION_CONFIRM_IMAGES = (
    r"UI\OK.bmp",
    r"UI\OK2.bmp",
    r"UI\Potion_Confirm.bmp",
)


def _noop_emit_data(payload: Mapping[str, Any]) -> None:
    return None


def leveling_potion_state_path() -> Path:
    return app_data_dir() / "leveling_potion_state.json"


def read_leveling_potion_payload(
    job: Job | str | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    current_job = _normalize_potion_job(job)
    state = _load_leveling_potion_state()
    if current_job is None:
        current_job = _normalize_potion_job(state.get("lastJob")) or Job.LYNN.value
    return _leveling_potion_payload_from_state(state, current_job, now=now)


def _load_leveling_potion_state() -> dict[str, Any]:
    path = leveling_potion_state_path()
    try:
        raw_state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"lastJob": Job.LYNN.value, "jobs": {}}

    if not isinstance(raw_state, dict):
        return {"lastJob": Job.LYNN.value, "jobs": {}}

    jobs: dict[str, dict[str, float]] = {}
    raw_jobs = raw_state.get("jobs")
    if isinstance(raw_jobs, dict):
        for raw_job, raw_entry in raw_jobs.items():
            job = _normalize_potion_job(raw_job)
            if job is None:
                continue
            timestamp = _coerce_potion_timestamp(
                raw_entry.get("lastUsedAt") if isinstance(raw_entry, dict) else raw_entry
            )
            if timestamp is not None:
                jobs[job] = {"lastUsedAt": timestamp}

    last_job = _normalize_potion_job(raw_state.get("lastJob")) or Job.LYNN.value
    return {"lastJob": last_job, "jobs": jobs}


def _write_leveling_potion_state(state: Mapping[str, Any]) -> None:
    path = leveling_potion_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(state), ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_potion_job(job: Any) -> str | None:
    if isinstance(job, Job):
        return job.value
    if isinstance(job, str):
        normalized = job.strip().lower()
        if normalized in {candidate.value for candidate in Job}:
            return normalized
    return None


def _coerce_potion_timestamp(value: Any) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return timestamp


def _potion_last_used_at(state: Mapping[str, Any], job: str) -> float | None:
    jobs = state.get("jobs")
    if not isinstance(jobs, dict):
        return None
    entry = jobs.get(job)
    if isinstance(entry, dict):
        return _coerce_potion_timestamp(entry.get("lastUsedAt"))
    return _coerce_potion_timestamp(entry)


def _record_leveling_potion_use(
    state: Mapping[str, Any],
    job: str,
    used_at: float,
) -> dict[str, Any]:
    jobs = dict(state.get("jobs")) if isinstance(state.get("jobs"), dict) else {}
    jobs[job] = {"lastUsedAt": float(used_at)}
    next_state = {"lastJob": job, "jobs": jobs}
    _write_leveling_potion_state(next_state)
    return next_state


def _remember_leveling_potion_job(state: Mapping[str, Any], job: str) -> dict[str, Any]:
    jobs = dict(state.get("jobs")) if isinstance(state.get("jobs"), dict) else {}
    next_state = {"lastJob": job, "jobs": jobs}
    _write_leveling_potion_state(next_state)
    return next_state


def _leveling_potion_payload_from_state(
    state: Mapping[str, Any],
    job: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    last_used_at = _potion_last_used_at(state, job)
    if now is None:
        now = time.time()
    minutes_since_last_use = (
        None
        if last_used_at is None
        else max(0, int((float(now) - last_used_at) // 60))
    )
    return {
        "potionJob": job,
        "potionLastUsedAt": last_used_at,
        "potionMinutesSinceLastUse": minutes_since_last_use,
        "potionIntervalMinutes": POTION_INTERVAL_MINUTES,
    }


@dataclass(frozen=True)
class LevelingScriptResult:
    exit_reason: str
    steps: int
    map_id: int


@dataclass(frozen=True)
class LevelingMapConfig:
    fountain_x: int
    fountain_y: int
    fountain_dir: int
    ball_pattern_type: int
    ball_cooldown: int
    attack_x: int
    attack_y: int
    attack_facing: int
    zone1_x: int
    zone1_y: int
    zone2_x: int
    zone2_y: int


class LevelingRunner(DailyRunner):
    def __init__(
        self,
        *,
        config: ProjectConfig,
        device: InputDevice,
        matcher: TemplateMatcher,
        sleeper: Sleeper,
        logger: logging.Logger,
        options: Mapping[str, Any] | None = None,
        control: RunControl | None = None,
        window_info: WindowInfo | None = None,
        capture: MssScreenCapture | None = None,
        request_pause: Callable[[], None] | None = None,
        rune_solver: RuneSolver | None = None,
        emit_data: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(
            config=config,
            device=device,
            matcher=matcher,
            sleeper=sleeper,
            logger=logger,
            options=dict(options or {}),
            control=control,
            window_info=window_info,
            capture=capture,
            request_pause=request_pause,
            rune_solver=rune_solver,
        )
        self.actions = CharacterActions(device, sleeper, logger)
        self.emit_data = emit_data or _noop_emit_data
        self._potion_state = _load_leveling_potion_state()
        self._last_potion_status_emit_at = 0.0

    def run(self) -> LevelingScriptResult:
        try:
            self.device.open()
            self._initialize_window()
            self._initialize_job()
            self._initialize_leveling_state()
            self._confirm_aut_map()
            self._run_leveling_loop()
            return self._leveling_result("completed")
        except StopRequested:
            self.logger.info("leveling_stop_requested steps=%s", self.steps)
            return self._leveling_result("stop_requested")
        finally:
            self.device.close()
            if self.capture is not None:
                self.capture.close()

    def _initialize_leveling_state(self) -> None:
        now = self._get_timestamp()
        self.vars.update(
            {
                "fountainTime": 0,
                "BallTime": 0,
                "yTolerance": 0,
                "stoneTime": 0,
                "yanusTime": 0,
                "Key7Time": 0,
                "RuneCooldown": 0,
                "RuneUiMissingAttempts": 0,
                "patrolTime": now,
                "map": 0,
            }
        )
        self._remember_current_potion_job()
        self._emit_potion_status(force=True)

    def _run_leveling_loop(self) -> None:
        while True:
            self._checkpoint()
            self.steps += 1
            if self.steps > 1_000_000:
                raise RuntimeError("Leveling execution step limit exceeded.")

            map_id = int(self.vars.get("map", 0))
            self._place_reincarnation_stone_if_ready()

            if map_id in SUPPORTED_LEVELING_MAPS:
                self._process_unified_map()
            else:
                self.logger.info("[Leveling] 当前地图未配置：%s", map_id)
                self.sleeper.delay_ms(200)

            self._release_rune_if_ready()

    def _place_reincarnation_stone_if_ready(self) -> None:
        if self._get_timestamp() - float(self.vars.get("stoneTime", 0)) <= 570:
            return
        log_important(self.logger, "按O键放轮回碑石")
        self._wait_stable_big()
        for index in range(4):
            self.actions.hold("O", 142)
            self.sleeper.delay_ms(413 if index < 3 else 681)
        self.vars["stoneTime"] = self._get_timestamp()

    def _wait_stable_big(self) -> None:
        controller = self._active_character_controller()
        if controller is None:
            return
        if not controller.wait_stable_big():
            self.logger.info("[Leveling] 放轮回碑石前未确认 Y 轴稳定，继续执行")

    def _process_unified_map(self) -> None:
        map_config = self._get_map_config()
        fountain_available = self._match_optional(
            "Leveling.FountainReady",
            (r"E:\MHImg\Common\1C.bmp",),
            self._region("x3", "y3", "xEnd", "yEnd"),
        )
        if (
            self._get_timestamp() - float(self.vars.get("fountainTime", 0)) > 55
            or fountain_available
        ):
            self._place_fountain(map_config)
            return

        ball_available = self._check_ball_pattern()
        if (
            self._get_timestamp() - float(self.vars.get("yanusTime", 0)) > map_config.ball_cooldown
            and ball_available
        ):
            self._process_ball_logic(map_config)
            return

        position = self._locate_character()
        if position is None:
            self._emit_potion_status_if_needed()
            self.sleeper.delay_ms(200)
            return

        in_attack_zone = self._is_in_attack_zone(position, map_config)
        if not in_attack_zone:
            log_important(self.logger, "回到攻击点")
            self._aut_navi(map_config.attack_x, map_config.attack_y, tolerance=4, y_tolerance=0)
            self._wiggle(map_config.attack_facing)
            self.vars["patrolTime"] = self._get_timestamp()
        else:
            self._use_potion_if_ready()

        if self._get_timestamp() - float(self.vars.get("patrolTime", 0)) > 9:
            log_important(self.logger, "左右晃防呆")
            self.sleeper.delay_random_ms(39, 42)
            self._wiggle(map_config.attack_facing)
            self.vars["patrolTime"] = self._get_timestamp()
            return

        if self._get_timestamp() - float(self.vars.get("Key7Time", 0)) > 120:
            log_important(self.logger, "按7键吃里程道具")
            self.device.press_key(keycode("7"), 1)
            self.vars["Key7Time"] = self._get_timestamp()

        controller = self._active_character_controller()
        if controller is not None:
            controller.stand_attack()
        self._emit_potion_status_if_needed()
        self.sleeper.delay_random_ms(632, 640)

    def _is_in_attack_zone(
        self,
        position: Any,
        map_config: LevelingMapConfig,
    ) -> bool:
        return (
            position.y == map_config.attack_y
            and map_config.attack_x - 4 <= position.x <= map_config.attack_x + 4
        )

    def _use_potion_if_ready(self) -> None:
        job = self._current_potion_job()
        if job is None:
            return

        now = self._wall_clock()
        last_used_at = _potion_last_used_at(self._potion_state, job)
        if last_used_at is not None and now - last_used_at < POTION_INTERVAL_SECONDS:
            self._emit_potion_status_if_needed()
            return

        log_important(self.logger, "[Leveling] %s 自动吃药", job)
        self.device.press_key(keycode("p"), 1)
        self.sleeper.delay_ms(1000)
        self.device.press_key(keycode("2"), 1)
        self.sleeper.delay_ms(200)
        self._confirm_potion_dialog_if_present()

        self._potion_state = _record_leveling_potion_use(
            self._potion_state,
            job,
            self._wall_clock(),
        )
        self._emit_potion_status(force=True)

    def _confirm_potion_dialog_if_present(self) -> None:
        for _ in range(2):
            match = self._match_optional(
                "Leveling.PotionDialog",
                POTION_CONFIRM_IMAGES,
                self._region("x1", "y1", "xEnd", "yEnd"),
            )
            if match is None:
                return
            log_important(self.logger, "[Leveling] 自动吃药确认弹窗：%s", match.image_path)
            self.device.press_key(keycode("enter"), 1)
            self.sleeper.delay_ms(200)

    def _current_potion_job(self) -> str | None:
        return _normalize_potion_job(self._active_job())

    def _remember_current_potion_job(self) -> None:
        job = self._current_potion_job()
        if job is None:
            return
        self._potion_state = _remember_leveling_potion_job(self._potion_state, job)

    def _emit_potion_status_if_needed(self) -> None:
        now = self._wall_clock()
        if now - self._last_potion_status_emit_at < 5:
            return
        self._emit_potion_status(now=now)

    def _emit_potion_status(self, *, force: bool = False, now: float | None = None) -> None:
        job = self._current_potion_job()
        if job is None:
            return
        if now is None:
            now = self._wall_clock()
        if not force and now - self._last_potion_status_emit_at < 5:
            return
        self.emit_data(_leveling_potion_payload_from_state(self._potion_state, job, now=now))
        self._last_potion_status_emit_at = now

    def _wall_clock(self) -> float:
        return time.time()

    def _place_fountain(self, map_config: LevelingMapConfig) -> None:
        log_important(self.logger, "前往放喷泉")
        self._aut_navi(map_config.fountain_x, map_config.fountain_y, tolerance=4)
        self.sleeper.delay_random_ms(56, 58)
        if map_config.fountain_dir == 1:
            self.actions.hold_random("Right", 62, 68)
            self.sleeper.delay_random_ms(106, 109)
        elif map_config.fountain_dir == 0:
            self.actions.press("Left")
            self.sleeper.delay_random_ms(56, 58)
        self.actions.hold_random("1", 194, 196)
        self.sleeper.delay_random_ms(712, 721)
        self.vars["fountainTime"] = self._get_timestamp()

    def _check_ball_pattern(self) -> bool:
        return (
            self._match_optional(
                "Leveling.BallPattern",
                (
                    r"E:\MHImg\Common\X_3.bmp",
                    r"E:\MHImg\Common\X_2.bmp",
                    r"E:\MHImg\Common\X_1.bmp",
                    r"E:\MHImg\Common\X_OK.bmp",
                ),
                self._region("x3", "y3", "xEnd", "yEnd"),
            )
            is not None
        )

    def _process_ball_logic(self, map_config: LevelingMapConfig) -> None:
        map_id = int(self.vars.get("map", 0))
        if map_id in {111, 121}:
            self._place_yanus_ball(
                1,
                map_config.attack_x - 31,
                map_config.attack_y - 2,
                update_timer=True,
            )
            self._place_yanus_ball(2, map_config.attack_x - 51, map_config.attack_y - 2)
        elif map_id == 122:
            self._place_yanus_ball(1, -81, 97, update_timer=True)
            self._place_yanus_ball(2, -81, 125)
            self._place_yanus_ball(3, 39, 80)
        elif map_id == 132:
            self._place_yanus_ball(1, -12, 106, update_timer=True)
            self._place_yanus_ball(2, -44, 106)
            self._place_yanus_ball(3, -90, 106)
        elif map_id == 101:
            self._place_yanus_ball(
                1,
                map_config.attack_x + 36,
                map_config.attack_y,
                update_timer=True,
            )
        elif map_id == 161:
            self._place_yanus_ball(
                1,
                33,
                104,
                update_timer=True,
                before_press=self._lynn_night_road_fallback_attack,
            )
            if self._active_job() == Job.LARA:
                self._aut_navi(45, 104, tolerance=2)
                self.sleeper.delay_ms(50)
            self._place_yanus_ball(2, 81, 104)
            self._place_yanus_ball(3, 117, 105)
        elif map_id == -232:
            self._place_yanus_ball(1, 115, 100, update_timer=True)
            self._place_yanus_ball(2, 68, 94)
            self._place_yanus_ball(3, 47, 94)

    def _place_yanus_ball(
        self,
        index: int,
        x: int,
        y: int,
        *,
        update_timer: bool = False,
        before_press: Callable[[], None] | None = None,
    ) -> None:
        log_important(self.logger, "前往放亚努斯(球),第%s个", index)
        self._aut_navi(x, y, tolerance=2)
        if before_press is not None:
            before_press()
        else:
            self.sleeper.delay_ms(50)
        self.device.press_key(keycode("x"), 1)
        if update_timer:
            self.vars["yanusTime"] = self._get_timestamp()
        self.sleeper.delay_ms(300)

    def _lynn_night_road_fallback_attack(self) -> None:
        if self._active_job() != Job.LYNN:
            self.sleeper.delay_ms(50)
            return
        self.sleeper.delay_ms(68)
        self.actions.hold("F", 1320)
        self.sleeper.delay_ms(381)

    def _aut_navi(
        self,
        target_x: int,
        target_y: int,
        *,
        tolerance: int,
        y_tolerance: int = 0,
    ) -> None:
        controller = self._active_character_controller()
        if controller is None:
            raise RuntimeError("Leveling requires an initialized Lara/Lynn controller.")
        target = MoveTarget(
            x=int(target_x),
            y=int(target_y),
            x_tolerance=int(tolerance),
            y_tolerance=int(y_tolerance),
        )
        result, _portal_route = move_with_portal_navigation(
            controller=controller,
            actions=self.actions,
            target=target,
            map_id=int(self.vars.get("map", 0)),
            logger=self.logger,
            position_sink=self._sync_character_position,
            log_prefix="[Leveling.Navi]",
        )
        if not result.reached:
            self.logger.warning(
                "[Leveling] 绉诲姩鍒?(%s,%s) 鏈畬鎴愶細%s",
                target_x,
                target_y,
                result.reason,
            )
        return

    def _locate_character(self):
        controller = self._active_character_controller()
        if controller is None:
            return None
        return controller.locate(recover=True)

    def _wiggle(self, attack_facing: int) -> None:
        if attack_facing == 0:
            self.actions.hold("Right", 88)
            self.sleeper.delay_random_ms(39, 42)
            self.actions.hold("Left", 88)
            self.sleeper.delay_random_ms(39, 42)
        else:
            self.actions.hold("Left", 88)
            self.sleeper.delay_random_ms(39, 42)
            self.actions.hold("Right", 88)
            self.sleeper.delay_random_ms(39, 42)

    def _release_rune_if_ready(self) -> None:
        self._release_rune_with_solver()

    def _prepare_rune_trigger_ui(self) -> bool:
        return True

    def _move_to_rune_verify_position(self) -> bool:
        try:
            map_config = self._get_map_config()
        except RuntimeError as exc:
            self.logger.warning("[解符文] 无法选择验证位置：%s", exc)
            return False
        self._aut_navi(map_config.attack_x, map_config.attack_y, tolerance=4, y_tolerance=0)
        self.sleeper.delay_ms(300)
        return True

    def _confirm_aut_map(self) -> int:
        self.vars["map"] = 0
        if self._match_map("AUT1"):
            self.vars["map"] = 101
        if self._match_map("AUT2"):
            self.vars["map"] = 111
        if self._match_map("AUT3"):
            self.vars["map"] = self._confirm_aut3_submap()
        if self._match_map("AUT4"):
            self.vars["map"] = self._confirm_aut4_submap()
        if self._match_map("AUT7"):
            self.vars["map"] = 161
        if self._match_map("CityWeek"):
            self.vars["map"] = -232
        log_important(self.logger, "[Leveling] 当前 AUT 地图：%s", self.vars["map"])
        return int(self.vars["map"])

    def _match_map(self, name: str) -> bool:
        return (
            self._match_optional(
                f"Leveling.Map.{name}",
                (
                    fr"E:\MHImg\Maps\{name}.bmp",
                    fr"Maps\{name}.bmp",
                ),
                self._region("x1", "y1", "x2", "y2"),
            )
            is not None
        )

    def _confirm_aut3_submap(self) -> int:
        teleport = self._teleport_position()
        if teleport == (-96, 100):
            return 121
        if teleport == (-105, 124):
            return 122
        return 0

    def _confirm_aut4_submap(self) -> int:
        teleport = self._teleport_position()
        if teleport == (-111, 118):
            return 132
        return 0

    def _teleport_position(self) -> tuple[int, int] | None:
        region = self._region("x1", "y1", "x2", "y2")
        anchor = self._match_optional("Leveling.MapAnchor", (r"E:\MHImg\MapAnchor.bmp",), region)
        teleport = self._match_optional("Leveling.Teleport", (r"E:\MHImg\Teleport.bmp",), region)
        if anchor is None or teleport is None:
            return None
        return teleport.x - anchor.x, teleport.y - anchor.y

    def _get_map_config(self) -> LevelingMapConfig:
        map_id = int(self.vars.get("map", 0))
        if map_id == 101:
            return LevelingMapConfig(32, 103, 1, 1, 55, -40, 103, 0, -15, 85, -40, 119)
        if map_id == 111:
            return LevelingMapConfig(20, 81, 1, 2, 57, 27, 94, 1, 12, 94, 12, 105)
        if map_id == 121:
            return LevelingMapConfig(-17, 88, -1, 2, 65, 36, 88, 0, 18, 88, 18, 101)
        if map_id == 122:
            return LevelingMapConfig(15, 80, 0, 3, 72, 23, 111, 0, 45, 111, 22, 125)
        if map_id == 132:
            return LevelingMapConfig(-77, 91, 1, 3, 72, 23, 106, 0, 31, 91, 23, 120)
        if map_id == 161:
            if self._active_job() == Job.LYNN:
                return LevelingMapConfig(141, 105, 1, 3, 82, 82, 104, 0, 141, 105, 141, 105)
            return LevelingMapConfig(141, 105, 1, 3, 102, 45, 104, 1, 141, 105, 141, 105)
        if map_id == -232:
            return LevelingMapConfig(152, 112, 1, 3, 102, 47, 94, 1, 115, 100, 68, 94)
        raise RuntimeError(f"Unsupported leveling map: {map_id}")

    def _leveling_result(self, exit_reason: str) -> LevelingScriptResult:
        return LevelingScriptResult(
            exit_reason=exit_reason,
            steps=self.steps,
            map_id=int(self.vars.get("map", 0)),
        )


def create_runner(
    *,
    config: ProjectConfig,
    dry_run: bool,
    skip_delays: bool,
    logger: logging.Logger,
    control: RunControl,
    options: Mapping[str, Any] | None = None,
    request_pause: Callable[[], None] | None = None,
    emit_data: Callable[[Mapping[str, Any]], None] | None = None,
) -> LevelingRunner:
    raw_device: InputDevice = (
        DryRunDevice(logger=logger) if dry_run else YjsDevice(settings=config.yjs, logger=logger)
    )
    device: InputDevice = ControlledInputDevice(raw_device, control)
    capture = MssScreenCapture()
    matcher = TemplateMatcher(capture=capture, logger=logger)
    effective_skip_delays = skip_delays and dry_run
    if skip_delays and not dry_run:
        logger.warning("[Timing] 实机模式已忽略“跳过等待”，按键按住时长必须保留以匹配 KM 行为。")
    sleeper = (
        NullSleeper(logger=logger, control=control)
        if effective_skip_delays
        else Sleeper(logger=logger, control=control)
    )
    return LevelingRunner(
        config=config,
        device=device,
        matcher=matcher,
        sleeper=sleeper,
        logger=logger,
        options=options,
        control=control,
        capture=capture,
        request_pause=request_pause,
        emit_data=emit_data,
    )
