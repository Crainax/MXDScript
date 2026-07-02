from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger

from mhscript_yjs.characters.actions import CharacterActions
from mhscript_yjs.characters.base import MoveResult, MoveTarget
from mhscript_yjs.characters.controller import CharacterController
from mhscript_yjs.characters.position import CharacterPosition

PositionSink = Callable[[CharacterPosition], None]
PORTAL_ENTRY_ATTEMPTS = 6
PORTAL_ROUTE_DIRECT_DISTANCE_MULTIPLIER = 1.5


@dataclass(frozen=True)
class PortalRoute:
    map_id: int
    entrance: tuple[int, int]
    exit: tuple[int, int]
    key: str = "Up"
    exit_x_tolerance: int = 12
    exit_y_tolerance: int = 3

    def exit_matches(self, position: CharacterPosition) -> bool:
        return (
            abs(position.x - self.exit[0]) <= self.exit_x_tolerance
            and abs(position.y - self.exit[1]) <= self.exit_y_tolerance
        )


@dataclass(frozen=True)
class PortalRouteDecision:
    route: PortalRoute
    direct_distance: int
    portal_distance: int


PORTAL_ROUTES = (
    PortalRoute(
        map_id=122,
        entrance=(28, 125),
        exit=(-94, 82),
    ),
    PortalRoute(
        map_id=122,
        entrance=(-78, 125),
        exit=(39, 80),
    ),
    PortalRoute(
        map_id=132,
        entrance=(28, 120),
        exit=(-89, 91),
    ),
    PortalRoute(
        map_id=132,
        entrance=(-89, 91),
        exit=(38, 91),
    ),
    PortalRoute(
        map_id=161,
        entrance=(140, 114),
        exit=(37, 94),
    ),
    PortalRoute(
        map_id=161,
        entrance=(37, 114),
        exit=(140, 95),
    ),
)


def find_portal_route(
    map_id: int,
    position: CharacterPosition,
    target: MoveTarget,
) -> PortalRoute | None:
    decision = choose_portal_route(map_id, position, target)
    return decision.route if decision is not None else None


def choose_portal_route(
    map_id: int,
    position: CharacterPosition,
    target: MoveTarget,
) -> PortalRouteDecision | None:
    direct_distance = _distance((position.x, position.y), (target.x, target.y))
    best: PortalRouteDecision | None = None
    for route in PORTAL_ROUTES:
        if route.map_id != map_id:
            continue
        portal_distance = _distance((position.x, position.y), route.entrance) + _distance(
            route.exit,
            (target.x, target.y),
        )
        if direct_distance <= portal_distance * PORTAL_ROUTE_DIRECT_DISTANCE_MULTIPLIER:
            continue
        decision = PortalRouteDecision(route, direct_distance, portal_distance)
        if best is None or decision.portal_distance < best.portal_distance:
            best = decision
    return best


def move_with_portal_navigation(
    *,
    controller: CharacterController,
    actions: CharacterActions,
    target: MoveTarget,
    map_id: int | None,
    logger: Logger,
    position_sink: PositionSink | None = None,
    log_prefix: str = "[Navi]",
) -> tuple[MoveResult, PortalRoute | None]:
    if map_id is None:
        logger.info("%s 未识别到传送门地图，使用标准 Move", log_prefix)
        result = controller.move_to(target)
        _sync_position(position_sink, result.last_position)
        return result, None

    position = controller.locate(recover=True)
    if position is None:
        logger.info("%s 无法定位角色，使用标准 Move", log_prefix)
        result = controller.move_to(target)
        _sync_position(position_sink, result.last_position)
        return result, None

    decision = choose_portal_route(map_id, position, target)
    if decision is None:
        logger.info(
            "%s map=%s 未命中更优传送门路线，使用标准 Move",
            log_prefix,
            map_id,
        )
        result = controller.move_to(target)
        _sync_position(position_sink, result.last_position)
        return result, None
    route = decision.route

    logger.info(
        "%s map=%s 命中传送门：current=(%s,%s) target=(%s,%s) entrance=(%s,%s) "
        "exit=(%s,%s) direct=%s portal=%s multiplier=%.2f",
        log_prefix,
        map_id,
        position.x,
        position.y,
        target.x,
        target.y,
        route.entrance[0],
        route.entrance[1],
        route.exit[0],
        route.exit[1],
        decision.direct_distance,
        decision.portal_distance,
        PORTAL_ROUTE_DIRECT_DISTANCE_MULTIPLIER,
    )
    entrance_result = controller.move_to(
        MoveTarget(route.entrance[0], route.entrance[1], x_tolerance=2, y_tolerance=0)
    )
    _sync_position(position_sink, entrance_result.last_position)
    if not entrance_result.reached:
        logger.warning(
            "%s 未能到达传送门入口 (%s,%s)：%s，继续标准 Move",
            log_prefix,
            route.entrance[0],
            route.entrance[1],
            entrance_result.reason,
        )
        final_result = controller.move_to(target)
        _sync_position(position_sink, final_result.last_position)
        return final_result, route

    _enter_portal(
        controller=controller,
        actions=actions,
        route=route,
        logger=logger,
        position_sink=position_sink,
        log_prefix=log_prefix,
    )

    final_result = controller.move_to(target)
    _sync_position(position_sink, final_result.last_position)
    return final_result, route


def _enter_portal(
    *,
    controller: CharacterController,
    actions: CharacterActions,
    route: PortalRoute,
    logger: Logger,
    position_sink: PositionSink | None,
    log_prefix: str,
) -> bool:
    current = controller.locate(recover=True)
    _sync_position(position_sink, current)
    for attempt in range(1, PORTAL_ENTRY_ATTEMPTS + 1):
        if current is None:
            logger.warning("%s 传送入口第 %s 次探测前未能定位角色", log_prefix, attempt)
            return False
        if route.exit_matches(current):
            logger.info("%s 传送点通过完成：(%s,%s)", log_prefix, current.x, current.y)
            return True

        direction = _portal_entry_direction(route, current)
        logger.info(
            "%s 传送入口探测 %s/%s：current=(%s,%s) entrance=(%s,%s) direction=%s",
            log_prefix,
            attempt,
            PORTAL_ENTRY_ATTEMPTS,
            current.x,
            current.y,
            route.entrance[0],
            route.entrance[1],
            direction or route.key,
        )
        _press_portal_entry(actions, route.key, direction, abs(route.entrance[0] - current.x))
        actions.delay_random(231, 234)

        current = controller.locate(recover=True)
        _sync_position(position_sink, current)

    if current is None:
        logger.warning(
            "%s 传送出口验证未命中：current=unknown expected=(%s,%s)，继续标准 Move",
            log_prefix,
            route.exit[0],
            route.exit[1],
        )
    elif route.exit_matches(current):
        logger.info("%s 传送点通过完成：(%s,%s)", log_prefix, current.x, current.y)
        return True
    else:
        logger.warning(
            "%s 传送出口验证未命中：current=(%s,%s) expected=(%s,%s)，继续标准 Move",
            log_prefix,
            current.x,
            current.y,
            route.exit[0],
            route.exit[1],
        )
    return False


def _portal_entry_direction(route: PortalRoute, position: CharacterPosition) -> str | None:
    if position.x < route.entrance[0]:
        return "Right"
    if position.x > route.entrance[0]:
        return "Left"
    return None


def _press_portal_entry(
    actions: CharacterActions,
    portal_key: str,
    direction: str | None,
    x_distance: int,
) -> None:
    if direction is None:
        _tap_portal_key(actions, portal_key, taps=2)
        return

    actions.key_down(direction)
    try:
        actions.delay(15)
        _tap_portal_key(actions, portal_key, taps=_portal_entry_tap_count(x_distance))
    finally:
        actions.key_up(direction)


def _tap_portal_key(actions: CharacterActions, portal_key: str, *, taps: int) -> None:
    for index in range(max(1, taps)):
        actions.press(portal_key)
        if index < taps - 1:
            actions.delay(45)


def _portal_entry_tap_count(x_distance: int) -> int:
    if x_distance <= 1:
        return 2
    if x_distance == 2:
        return 3
    return 4


def _distance(first: tuple[int, int], second: tuple[int, int]) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])


def _sync_position(position_sink: PositionSink | None, position: CharacterPosition | None) -> None:
    if position_sink is not None and position is not None:
        position_sink(position)
