from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mhscript_yjs.characters import CharacterPosition, MoveTarget
from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.navigation import PortalRoute, move_with_portal_navigation
from mhscript_yjs.runtime.control import StopRequested
from mhscript_yjs.runtime.logging import log_important
from mhscript_yjs.scripts.daily.combine_main import create_runner as create_daily_runner

if TYPE_CHECKING:
    from mhscript_yjs.scripts.registry import ScriptRunContext


COORDINATE_MOVER_SCRIPT_ID = "coordinate_mover"
DEFAULT_COORDINATE_MOVER_OPTIONS = {
    "targetX": "",
    "targetY": "",
    "moveMode": "MoveB",
}


@dataclass(frozen=True)
class CoordinateMoverResult:
    exit_reason: str
    iterations: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoordinateTarget:
    x: int
    y: int


def run_coordinate_mover(context: ScriptRunContext) -> CoordinateMoverResult:
    target = _coerce_target(context.script_options)
    move_mode = _coerce_move_mode(context.script_options.get("moveMode", "MoveB"))
    if target is None:
        payload = {
            "kind": "coordinateMover",
            "status": "invalidTarget",
            "message": "请先填写目标坐标 X/Y",
            "moveStatus": {
                "state": "failed",
                "message": "请先填写目标坐标 X/Y",
                "moveMode": move_mode,
            },
        }
        context.emit_data(payload)
        log_important(context.logger, "[移动坐标] 未填写有效目标坐标，脚本结束")
        return CoordinateMoverResult(exit_reason="invalid_target", details=payload)

    runner = create_daily_runner(
        config=context.config,
        dry_run=context.dry_run,
        skip_delays=context.skip_delays,
        logger=context.logger,
        control=context.control,
        options={},
        request_pause=context.request_pause,
    )
    context.emit_data(
        {
            "kind": "coordinateMover",
            "status": "running",
            "message": f"准备使用 {move_mode} 前往 ({target.x},{target.y})",
            "moveStatus": _move_status(
                "running",
                target,
                move_mode,
                f"准备使用 {move_mode} 前往目标坐标",
            ),
        }
    )
    log_important(
        context.logger,
        "[移动坐标] 开始：mode=%s target=(%s,%s)",
        move_mode,
        target.x,
        target.y,
    )

    try:
        runner.device.open()
        runner._initialize_window()
        runner._initialize_job()
        job = runner._active_job()
        controller = (
            runner._active_move_only_controller()
            if move_mode == "MoveB"
            else runner._active_character_controller()
        )
        if job is None or controller is None:
            raise RuntimeError("未检测到 Lynn/Lara 职业信息，无法移动坐标。")

        move_target = MoveTarget(target.x, target.y, x_tolerance=2, y_tolerance=1)
        map_id: int | None = None
        portal_route: PortalRoute | None = None
        if move_mode == "Navi":
            map_id = _detect_navi_map(runner)
            result, portal_route = move_with_portal_navigation(
                controller=controller,
                actions=CharacterActions(runner.device, runner.sleeper, context.logger),
                target=move_target,
                map_id=map_id,
                logger=context.logger,
                position_sink=runner._sync_character_position,
                log_prefix="[移动坐标.Navi]",
            )
        else:
            result = controller.move_to(move_target)
            if result.last_position is not None:
                runner._sync_character_position(result.last_position)

        state = "reached" if result.reached else "failed"
        message = "已到达目标坐标" if result.reached else f"未到达目标坐标：{result.reason}"
        payload = {
            "kind": "coordinateMover",
            "status": state,
            "message": message,
            "moveStatus": _move_status(
                state,
                target,
                move_mode,
                message,
                job=job.value,
                attempts=result.attempts,
                last_position=result.last_position,
                map_id=map_id,
                portal_route=portal_route,
            ),
        }
        context.emit_data(payload)
        log_important(context.logger, "[移动坐标] %s", message)
        return CoordinateMoverResult(
            exit_reason="completed" if result.reached else "failed",
            iterations=result.attempts,
            details=payload,
        )
    except StopRequested:
        payload = {
            "kind": "coordinateMover",
            "status": "stopped",
            "message": "移动坐标已停止",
            "moveStatus": _move_status("stopped", target, move_mode, "移动坐标已停止"),
        }
        context.emit_data(payload)
        context.logger.info("[移动坐标] stop requested")
        return CoordinateMoverResult(exit_reason="stop_requested", details=payload)
    except Exception as exc:
        message = f"{exc.__class__.__name__}: {exc}"
        payload = {
            "kind": "coordinateMover",
            "status": "failed",
            "message": message,
            "moveStatus": _move_status("failed", target, move_mode, message),
        }
        context.emit_data(payload)
        context.logger.exception("[移动坐标] 失败：%s", exc)
        return CoordinateMoverResult(exit_reason="error", details=payload)
    finally:
        try:
            runner.device.release_all_keys()
        except Exception as exc:  # pragma: no cover - best-effort hardware cleanup
            context.logger.warning("[移动坐标] release_all_keys 失败：%s", exc)
        runner.device.close()
        if runner.capture is not None:
            runner.capture.close()


def _coerce_target(options: Mapping[str, Any]) -> CoordinateTarget | None:
    raw_x = str(options.get("targetX", "")).strip()
    raw_y = str(options.get("targetY", "")).strip()
    if not raw_x or not raw_y:
        return None
    try:
        return CoordinateTarget(x=int(float(raw_x)), y=int(float(raw_y)))
    except ValueError:
        return None


def _coerce_move_mode(value: Any) -> str:
    text = str(value).strip().lower()
    if text == "navi":
        return "Navi"
    if text == "move":
        return "Move"
    return "MoveB"


def _detect_navi_map(runner: Any) -> int | None:
    if _match_navi_map(runner, "AUT3"):
        teleport = _navi_teleport_position(runner)
        if teleport == (-96, 100):
            return 121
        if teleport == (-105, 124):
            return 122
        return None
    if _match_navi_map(runner, "AUT4"):
        if _navi_teleport_position(runner) == (-111, 118):
            return 132
        return None
    if _match_navi_map(runner, "AUT7"):
        return 161
    return None


def _match_navi_map(runner: Any, name: str) -> bool:
    return (
        runner._match_optional(
            f"CoordinateMover.Map.{name}",
            (
                fr"E:\MHImg\Maps\{name}.bmp",
                fr"Maps\{name}.bmp",
            ),
            runner._region("x1", "y1", "x2", "y2"),
        )
        is not None
    )


def _navi_teleport_position(runner: Any) -> tuple[int, int] | None:
    region = runner._region("x1", "y1", "x2", "y2")
    anchor = runner._match_optional(
        "CoordinateMover.MapAnchor",
        (r"E:\MHImg\MapAnchor.bmp",),
        region,
    )
    teleport = runner._match_optional(
        "CoordinateMover.Teleport",
        (r"E:\MHImg\Teleport.bmp",),
        region,
    )
    if anchor is None or teleport is None:
        return None
    return teleport.x - anchor.x, teleport.y - anchor.y


def _move_status(
    state: str,
    target: CoordinateTarget,
    move_mode: str,
    message: str,
    *,
    job: str | None = None,
    attempts: int | None = None,
    last_position: CharacterPosition | None = None,
    map_id: int | None = None,
    portal_route: PortalRoute | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "state": state,
        "targetX": target.x,
        "targetY": target.y,
        "moveMode": move_mode,
        "message": message,
    }
    if job is not None:
        status["job"] = job
    if attempts is not None:
        status["attempts"] = attempts
    if map_id is not None:
        status["mapId"] = map_id
    if portal_route is not None:
        status["portal"] = {
            "fromX": portal_route.entrance[0],
            "fromY": portal_route.entrance[1],
            "toX": portal_route.exit[0],
            "toY": portal_route.exit[1],
        }
    if last_position is not None:
        status["lastPosition"] = {
            "x": last_position.x,
            "y": last_position.y,
            "screenX": last_position.screen_x,
            "screenY": last_position.screen_y,
        }
    return status
