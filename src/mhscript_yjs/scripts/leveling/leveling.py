from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from mhscript_yjs.characters import MoveTarget
from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import Job
from mhscript_yjs.core.config import ProjectConfig
from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.drivers.yjs import YjsDevice
from mhscript_yjs.runtime.control import RunControl, StopRequested
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.runtime.timing import NullSleeper, Sleeper
from mhscript_yjs.scripts.daily.combine_main import DAILY_MATCH_THRESHOLD, DailyRunner
from mhscript_yjs.vision.matcher import TemplateMatcher
from mhscript_yjs.vision.screenshot import MssScreenCapture
from mhscript_yjs.vision.types import ImageGroup, MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo


LEVELING_SCRIPT_ID = "leveling"
LEVELING_SCRIPT_NAME = "练级"
SUPPORTED_LEVELING_MAPS = {101, 111, 121, 122, 132, 161, -232}


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
        )
        self.actions = CharacterActions(device, sleeper, logger)

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
                "pick1Time": now,
                "Key7Time": 0,
                "RuneCooldown": 0,
                "patrolTime": now,
                "map": 0,
            }
        )

    def _run_leveling_loop(self) -> None:
        while True:
            self._checkpoint()
            self.steps += 1
            if self.steps > 1_000_000:
                raise RuntimeError("Leveling execution step limit exceeded.")

            map_id = int(self.vars.get("map", 0))
            if map_id < 0:
                self.execute_sub("Accept")

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
        if self._get_timestamp() - float(self.vars.get("fountainTime", 0)) > 55 or fountain_available:
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
            self.sleeper.delay_ms(200)
            return

        if (
            position.y != map_config.attack_y
            or position.x < map_config.attack_x - 4
            or position.x > map_config.attack_x + 4
        ):
            log_important(self.logger, "回到攻击点")
            self._aut_navi(map_config.attack_x, map_config.attack_y, tolerance=4, y_tolerance=0)
            self._wiggle(map_config.attack_facing)
            self.vars["patrolTime"] = self._get_timestamp()

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
        self.sleeper.delay_random_ms(632, 640)

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
            self._place_yanus_ball(1, map_config.attack_x - 31, map_config.attack_y - 2, update_timer=True)
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
            self._place_yanus_ball(1, map_config.attack_x + 36, map_config.attack_y, update_timer=True)
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
        map_id = int(self.vars.get("map", 0))
        position = self._locate_character()
        if position is not None:
            if map_id == 122:
                if target_x <= -39 and position.x >= -7:
                    self._move_via_portal(28, 125, -94, 82)
                elif target_x >= -7 and position.x <= -39:
                    if self._get_timestamp() - float(self.vars.get("pick1Time", 0)) > 65 and target_y >= 105:
                        self.vars["pick1Time"] = self._get_timestamp()
                    else:
                        self._move_via_portal(-78, 125, 39, 80)
            elif map_id == 132:
                if target_x < -55 and position.x > 2:
                    self._move_via_portal(28, 120, -89, 91)
                elif target_x > 2 and position.x < -55:
                    self._move_via_portal(-89, 91, 38, 91)
            elif map_id == 141:
                if target_x <= -49 and position.x >= 27:
                    self._move_via_portal(40, 92, -71, 97)
                elif target_x >= 27 and position.x <= -49:
                    self._move_via_portal(-71, 97, 40, 92)
            elif map_id == 161:
                if target_x <= 73 and position.x >= 126:
                    self._move_via_portal(140, 114, 37, 94)
                elif target_x >= 126 and position.x <= 73:
                    self._move_via_portal(37, 114, 140, 95)

        self._move_to(target_x, target_y, x_tolerance=tolerance, y_tolerance=y_tolerance)

    def _move_via_portal(self, approach_x: int, approach_y: int, exit_x: int, exit_y: int) -> None:
        self._move_to(approach_x, approach_y, x_tolerance=2, y_tolerance=0)
        self.actions.hold_random("Up", 120, 122)
        self.sleeper.delay_random_ms(231, 234)
        position = self._locate_character()
        if position is not None and abs(position.x - exit_x) <= 12 and abs(position.y - exit_y) <= 3:
            self.logger.info("[Leveling] 传送点通过完成：(%s,%s)", position.x, position.y)

    def _move_to(self, x: int, y: int, *, x_tolerance: int, y_tolerance: int = 0) -> None:
        controller = self._active_character_controller()
        if controller is None:
            raise RuntimeError("Leveling requires an initialized Lara/Lynn controller.")
        result = controller.move_to(
            MoveTarget(
                x=int(x),
                y=int(y),
                x_tolerance=int(x_tolerance),
                y_tolerance=int(y_tolerance),
            )
        )
        if result.last_position is not None:
            self._sync_character_position(result.last_position)
        if not result.reached:
            self.logger.warning("[Leveling] 移动到 (%s,%s) 未完成：%s", x, y, result.reason)

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
        if self._get_timestamp() - float(self.vars.get("RuneCooldown", 0)) <= 900:
            return
        rune = self._match_optional(
            "Leveling.Rune",
            (r"E:\MHImg\Rune.bmp",),
            self._region("x1", "y1", "x2", "y2"),
        )
        if rune is None:
            return
        log_important(self.logger, "前往解轮")
        self.execute_sub("ReleaseRune")

    def _confirm_aut_map(self) -> int:
        self.vars["map"] = 0
        if self._match_map("ARC4"):
            self.vars["map"] = 41
        if self._match_map("AUT1"):
            self.vars["map"] = 101
        if self._match_map("AUT2"):
            self.vars["map"] = 111
        if self._match_map("AUT3"):
            self.vars["map"] = self._confirm_aut3_submap()
        if self._match_map("AUT4"):
            self.vars["map"] = self._confirm_aut4_submap()
        if self._match_map("AUT5"):
            self.vars["map"] = 141
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
                    fr"UI\F2\Map\{name}.bmp",
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
        return 120

    def _confirm_aut4_submap(self) -> int:
        teleport = self._teleport_position()
        if teleport == (-111, 118):
            return 132
        return 131

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

    def _match_optional(
        self,
        name: str,
        raw_paths: tuple[str, ...],
        region: Region,
        threshold: float = DAILY_MATCH_THRESHOLD,
    ) -> MatchResult | None:
        self._checkpoint()
        paths = tuple(path for path in (self._resolve_image_path(raw) for raw in raw_paths) if path.exists())
        if not paths:
            self.logger.debug("leveling_match_skipped_missing_templates name=%s raw_paths=%s", name, raw_paths)
            return None
        return self.matcher.match_any(ImageGroup(name=name, paths=paths, threshold=threshold), region)

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
) -> LevelingRunner:
    device: InputDevice = (
        DryRunDevice(logger=logger) if dry_run else YjsDevice(settings=config.yjs, logger=logger)
    )
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
    )
