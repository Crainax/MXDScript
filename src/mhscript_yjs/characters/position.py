from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger

from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.timing import Sleeper
from mhscript_yjs.vision.types import MatchResult, Region
from mhscript_yjs.windows.maple import WindowInfo

ImageMatchFn = Callable[[str, tuple[str, ...], Region, float], MatchResult | None]
CoordinateMatchFn = Callable[[Region, float], tuple[MatchResult | None, MatchResult | None]]
PositionSink = Callable[["CharacterPosition"], None]
WindowProvider = Callable[[], WindowInfo]


@dataclass(frozen=True)
class CharacterPosition:
    x: int
    y: int
    screen_x: int
    screen_y: int
    anchor_screen_x: int
    anchor_screen_y: int


@dataclass
class PositionTracker:
    window: WindowInfo
    match_image: ImageMatchFn
    device: InputDevice
    sleeper: Sleeper
    logger: Logger
    position_sink: PositionSink | None = None
    window_provider: WindowProvider | None = None
    match_coordinates: CoordinateMatchFn | None = None
    cached_miss_limit: int = 3
    _last_position: CharacterPosition | None = None
    _last_anchor: MatchResult | None = None
    _last_window_key: tuple[int, int, int, int] | None = None
    _cached_misses: int = 0

    def locate(self, *, recover: bool = True, use_cache: bool = True) -> CharacterPosition | None:
        self.refresh_window()
        self._reset_cache_if_window_changed()
        region = self.minimap_region()
        me, anchor = self._match_position_pair(
            region,
            use_cached_anchor=not recover and not use_cache,
        )
        if use_cache and (me is None or anchor is None):
            cached = self._cached_position()
            if cached is not None:
                return cached

        if me is None and recover:
            self.logger.info("[Position] 未检测到人物坐标，尝试左右微调后重试")
            self._nudge("Left")
            region = self.minimap_region()
            me, anchor = self._match_position_pair(region)
            if use_cache and (me is None or anchor is None):
                cached = self._cached_position()
                if cached is not None:
                    return cached
            if me is None:
                self._nudge("Right")
                region = self.minimap_region()
                me, anchor = self._match_position_pair(region)
                if use_cache and (me is None or anchor is None):
                    cached = self._cached_position()
                    if cached is not None:
                        return cached

        if me is None or anchor is None:
            self._log_locate_failure(recover=recover, use_cache=use_cache, me=me, anchor=anchor)
            if use_cache:
                self._last_position = None
                self._cached_misses = 0
            return None

        position = CharacterPosition(
            x=me.x - anchor.x,
            y=me.y - anchor.y,
            screen_x=me.x,
            screen_y=me.y,
            anchor_screen_x=anchor.x,
            anchor_screen_y=anchor.y,
        )
        self._last_position = position
        self._last_anchor = anchor
        self._cached_misses = 0
        self.logger.info(
            "[Position] 人物坐标=(%s,%s) screen=(%s,%s) anchor=(%s,%s)",
            position.x,
            position.y,
            position.screen_x,
            position.screen_y,
            position.anchor_screen_x,
            position.anchor_screen_y,
        )
        if self.position_sink is not None:
            self.position_sink(position)
        return position

    def minimap_region(self) -> Region:
        self.refresh_window()
        return Region.from_bounds(
            self.window.x,
            self.window.y,
            self.window.x + 400,
            self.window.y + 330,
        )

    def skill_region(self) -> Region:
        self.refresh_window()
        return Region.from_bounds(
            self.window.right - 600,
            self.window.bottom - 105,
            self.window.right,
            self.window.bottom,
        )

    def _match_position_pair(
        self,
        region: Region,
        *,
        use_cached_anchor: bool = False,
    ) -> tuple[MatchResult | None, MatchResult | None]:
        if use_cached_anchor and self._last_anchor is not None:
            return self._match_me(region), self._last_anchor
        if self.match_coordinates is not None:
            me, anchor = self.match_coordinates(region, 1.0)
        else:
            me, anchor = self._match_me(region), self._match_anchor(region)
        if anchor is not None:
            self._last_anchor = anchor
        return me, anchor

    def _match_me(self, region: Region) -> MatchResult | None:
        return self.match_image(
            "Character.Me",
            (r"E:\MHImg\Me.bmp",),
            region,
            1.0,
        )

    def _match_anchor(self, region: Region) -> MatchResult | None:
        return self.match_image(
            "Character.MapAnchor",
            (r"E:\MHImg\MapAnchor.bmp",),
            region,
            1.0,
        )

    def _nudge(self, direction: str) -> None:
        self.device.key_down(keycode(direction))
        try:
            self.sleeper.delay_random_ms(200, 250)
        finally:
            self.device.key_up(keycode(direction))

    def _log_locate_failure(
        self,
        *,
        recover: bool,
        use_cache: bool,
        me: MatchResult | None,
        anchor: MatchResult | None,
    ) -> None:
        me_status = "yes" if me else "no"
        anchor_status = "yes" if anchor else "no"
        if not recover and not use_cache:
            self.logger.info(
                "[Position] 实时定位本帧未命中 me=%s anchor=%s",
                me_status,
                anchor_status,
            )
            return
        self.logger.warning(
            "[Position] 定位失败 me=%s anchor=%s",
            me_status,
            anchor_status,
        )

    def refresh_window(self) -> None:
        if self.window_provider is not None:
            self.window = self.window_provider()

    def _reset_cache_if_window_changed(self) -> None:
        window_key = (self.window.x, self.window.y, self.window.width, self.window.height)
        if self._last_window_key is not None and self._last_window_key != window_key:
            self._last_position = None
            self._last_anchor = None
            self._cached_misses = 0
        self._last_window_key = window_key

    def _cached_position(self) -> CharacterPosition | None:
        if self._last_position is None:
            return None
        self._cached_misses += 1
        if self._cached_misses >= max(1, self.cached_miss_limit):
            self.logger.warning("[Position] 坐标连续丢失 %s 次，缓存失效", self._cached_misses)
            self._last_position = None
            self._cached_misses = 0
            return None
        self.logger.info(
            "[Position] 本帧坐标丢失，沿用缓存坐标=(%s,%s) cached:%s",
            self._last_position.x,
            self._last_position.y,
            self._cached_misses,
        )
        if self.position_sink is not None:
            self.position_sink(self._last_position)
        return self._last_position
