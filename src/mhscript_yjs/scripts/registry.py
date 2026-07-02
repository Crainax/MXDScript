from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from mhscript_yjs.core.config import ProjectConfig
from mhscript_yjs.runtime.control import RunControl
from mhscript_yjs.runtime.timing import NullSleeper, Sleeper
from mhscript_yjs.scripts.daily.combine_main import (
    DAILY_SCRIPT_ID,
    DEFAULT_DAILY_OPTIONS,
)
from mhscript_yjs.scripts.daily.combine_main import (
    create_runner as create_daily_runner,
)
from mhscript_yjs.scripts.leveling.leveling import (
    DEFAULT_LEVELING_OPTIONS,
    LEVELING_SCRIPT_ID,
    LEVELING_SCRIPT_NAME,
    read_leveling_options_from_settings,
)
from mhscript_yjs.scripts.leveling.leveling import (
    create_runner as create_leveling_runner,
)
from mhscript_yjs.scripts.placeholders import run_placeholder_script
from mhscript_yjs.scripts.tool.coordinate_mover import (
    COORDINATE_MOVER_SCRIPT_ID,
    DEFAULT_COORDINATE_MOVER_OPTIONS,
    run_coordinate_mover,
)
from mhscript_yjs.scripts.tool.image_debug import (
    COORDINATE_DETECTOR_SCRIPT_ID,
    DEFAULT_COORDINATE_DETECTOR_OPTIONS,
    DEFAULT_IMAGE_RECOGNITION_OPTIONS,
    IMAGE_RECOGNITION_SCRIPT_ID,
    run_coordinate_detector,
    run_image_recognition,
)
from mhscript_yjs.scripts.tool.open_package import create_runner as create_open_package_runner
from mhscript_yjs.scripts.tool.rune_capture import (
    DEFAULT_RUNE_CAPTURE_OPTIONS,
    RUNE_CAPTURE_SCRIPT_ID,
    run_rune_capture,
)


def _noop_emit_data(payload: Mapping[str, Any]) -> None:
    return None


def _noop_request_pause() -> None:
    return None


@dataclass(frozen=True)
class ScriptRunContext:
    config: ProjectConfig
    logger: logging.Logger
    control: RunControl
    dry_run: bool
    skip_delays: bool
    script_options: Mapping[str, Any] = field(default_factory=dict)
    emit_data: Callable[[Mapping[str, Any]], None] = _noop_emit_data
    request_pause: Callable[[], None] = _noop_request_pause


@dataclass(frozen=True)
class ScriptRunResult:
    exit_reason: str
    iterations: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


ScriptRunner = Callable[[ScriptRunContext], ScriptRunResult]


@dataclass(frozen=True)
class ScriptDefinition:
    id: str
    name: str
    category: str
    description: str
    module: str
    default_shortcut: str
    runner: ScriptRunner
    placeholder: bool = False
    requires_mouse_precision: bool = True
    default_options: Mapping[str, Any] = field(default_factory=dict)


def get_script_definitions() -> tuple[ScriptDefinition, ...]:
    return tuple(
        definition
        for definition in (
        ScriptDefinition(
            id=LEVELING_SCRIPT_ID,
            name=LEVELING_SCRIPT_NAME,
            category="角色",
            description="自动执行 AUT 练级循环：喷泉、亚努斯、攻击点、防呆、轮回碑石和符文暂停。",
            module="mhscript_yjs.scripts.leveling.leveling",
            default_shortcut="Ctrl+F10",
            runner=_run_leveling_script,
            placeholder=False,
            requires_mouse_precision=False,
            default_options=DEFAULT_LEVELING_OPTIONS,
        ),
        ScriptDefinition(
            id=DAILY_SCRIPT_ID,
            name="日常脚本",
            category="日常",
            description="按模块执行每日任务、菇菇神社、活动签到和其他每日。",
            module="mhscript_yjs.scripts.daily.combine_main",
            default_shortcut="Ctrl+F9",
            runner=_run_daily_script,
            placeholder=False,
            requires_mouse_precision=True,
            default_options=DEFAULT_DAILY_OPTIONS,
        ),
        ScriptDefinition(
            id="open_package",
            name="自动开包",
            category="工具",
            description="自动开怪怪卡牌包并转成精华",
            module="mhscript_yjs.scripts.tool.open_package",
            default_shortcut="",
            runner=_run_open_package,
            placeholder=False,
            requires_mouse_precision=True,
        ),
        ScriptDefinition(
            id=IMAGE_RECOGNITION_SCRIPT_ID,
            name="识别图片",
            category="测试",
            description="按指定图片路径持续检测命中坐标。",
            module="mhscript_yjs.scripts.tool.image_debug",
            default_shortcut="",
            runner=_run_image_recognition,
            placeholder=False,
            requires_mouse_precision=False,
            default_options=DEFAULT_IMAGE_RECOGNITION_OPTIONS,
        ),
        ScriptDefinition(
            id=COORDINATE_DETECTOR_SCRIPT_ID,
            name="检测坐标",
            category="测试",
            description="持续检测 Me、Teleport 相对 MapAnchor 的坐标。",
            module="mhscript_yjs.scripts.tool.image_debug",
            default_shortcut="",
            runner=_run_coordinate_detector,
            placeholder=False,
            requires_mouse_precision=False,
            default_options=DEFAULT_COORDINATE_DETECTOR_OPTIONS,
        ),
        ScriptDefinition(
            id=COORDINATE_MOVER_SCRIPT_ID,
            name="移动坐标",
            category="测试",
            description="按目标坐标执行一次 Move、MoveB 或 Navi 移动。",
            module="mhscript_yjs.scripts.tool.coordinate_mover",
            default_shortcut="",
            runner=_run_coordinate_mover,
            placeholder=False,
            requires_mouse_precision=False,
            default_options=DEFAULT_COORDINATE_MOVER_OPTIONS,
        ),
        ScriptDefinition(
            id=RUNE_CAPTURE_SCRIPT_ID,
            name="符文截图采样",
            category="测试",
            description="定时截取游戏窗口，并实时显示四个符文槽位裁剪和识别标签。",
            module="mhscript_yjs.scripts.tool.rune_capture",
            default_shortcut="Ctrl+F7",
            runner=_run_rune_capture,
            placeholder=False,
            requires_mouse_precision=False,
            default_options=DEFAULT_RUNE_CAPTURE_OPTIONS,
        ),
        _placeholder(
            script_id="event_placeholder",
            name="活动脚本占位",
            category="活动",
            default_shortcut="F8",
        ),
        _placeholder(
            script_id="character_placeholder",
            name="角色循环占位",
            category="角色",
            default_shortcut="F9",
        ),
        _placeholder(
            script_id="system_placeholder",
            name="系统辅助占位",
            category="系统",
            default_shortcut="Ctrl+F12",
        ),
        )
        if not definition.placeholder
    )


def _run_image_recognition(context: ScriptRunContext) -> ScriptRunResult:
    result = run_image_recognition(context)
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.iterations,
        details=dict(result.details),
    )


def _run_coordinate_detector(context: ScriptRunContext) -> ScriptRunResult:
    result = run_coordinate_detector(context)
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.iterations,
        details=dict(result.details),
    )


def _run_coordinate_mover(context: ScriptRunContext) -> ScriptRunResult:
    result = run_coordinate_mover(context)
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.iterations,
        details=dict(result.details),
    )


def _run_rune_capture(context: ScriptRunContext) -> ScriptRunResult:
    result = run_rune_capture(context)
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.iterations,
        details=dict(result.details),
    )


def _run_open_package(context: ScriptRunContext) -> ScriptRunResult:
    context.logger.info("自动开包脚本准备启动。")
    runner = create_open_package_runner(
        config=context.config,
        dry_run=context.dry_run,
        skip_delays=context.skip_delays,
        logger=context.logger,
        control=context.control,
    )
    result = runner.run()
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.iterations,
        details={
            "no_find_count": result.no_find_count,
            "cards_opened": result.cards_opened,
        },
    )


def _run_leveling_script(context: ScriptRunContext) -> ScriptRunResult:
    context.logger.info("练级脚本准备启动：options=%s", dict(context.script_options))
    runner = create_leveling_runner(
        config=context.config,
        dry_run=context.dry_run,
        skip_delays=context.skip_delays,
        logger=context.logger,
        control=context.control,
        options=dict(context.script_options),
        options_provider=read_leveling_options_from_settings,
        request_pause=context.request_pause,
        emit_data=context.emit_data,
    )
    result = runner.run()
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.steps,
        details={"map": result.map_id},
    )


def _run_daily_script(context: ScriptRunContext) -> ScriptRunResult:
    context.logger.info("日常脚本准备启动：options=%s", dict(context.script_options))
    runner = create_daily_runner(
        config=context.config,
        dry_run=context.dry_run,
        skip_delays=context.skip_delays,
        logger=context.logger,
        control=context.control,
        options=dict(context.script_options),
        request_pause=context.request_pause,
    )
    result = runner.run()
    return ScriptRunResult(
        exit_reason=result.exit_reason,
        iterations=result.steps,
        details={"modules": result.modules},
    )


def _placeholder(
    *,
    script_id: str,
    name: str,
    category: str,
    default_shortcut: str,
) -> ScriptDefinition:
    def runner(context: ScriptRunContext) -> ScriptRunResult:
        sleeper = (
            NullSleeper(logger=context.logger, control=context.control)
            if context.skip_delays
            else Sleeper(logger=context.logger, control=context.control)
        )
        result = run_placeholder_script(
            display_name=name,
            control=context.control,
            sleeper=sleeper,
        )
        return ScriptRunResult(exit_reason=result.exit_reason, iterations=result.iterations)

    return ScriptDefinition(
        id=script_id,
        name=name,
        category=category,
        description="占位脚本，用于保留脚本库位置，后续可替换为真实 Python 脚本。",
        module="mhscript_yjs.scripts.placeholders",
        default_shortcut=default_shortcut,
        runner=runner,
        placeholder=True,
        requires_mouse_precision=False,
    )
