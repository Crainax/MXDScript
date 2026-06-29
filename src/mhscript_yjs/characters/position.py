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

    def locate(self, *, recover: bool = True) -> CharacterPosition | None:
        self.refresh_window()
        me = self._match_me()
        if me is None and recover:
            self.logger.warning("[Position] 未检测到人物坐标，尝试左右微调后重试")
            self._nudge("Left")
            me = self._match_me()
            if me is None:
                self._nudge("Right")
                me = self._match_me()
            if me is None:
                self.device.move_to(self.window.x + self.window.width // 2, self.window.y + self.window.height // 2)

        anchor = self._match_anchor()
        if me is None or anchor is None:
            self.logger.warning(
                "[Position] 定位失败 me=%s anchor=%s",
                "yes" if me else "no",
                "yes" if anchor else "no",
            )
            return None

        position = CharacterPosition(
            x=me.x - anchor.x,
            y=me.y - anchor.y,
            screen_x=me.x,
            screen_y=me.y,
            anchor_screen_x=anchor.x,
            anchor_screen_y=anchor.y,
        )
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

    def _match_me(self) -> MatchResult | None:
        return self.match_image(
            "Character.Me",
            (r"E:\MHImg\Me.bmp",),
            self.minimap_region(),
            1.0,
        )

    def _match_anchor(self) -> MatchResult | None:
        return self.match_image(
            "Character.MapAnchor",
            (r"E:\MHImg\MapAnchor.bmp",),
            self.minimap_region(),
            1.0,
        )

    def _nudge(self, direction: str) -> None:
        self.device.key_down(keycode(direction))
        try:
            self.sleeper.delay_random_ms(200, 250)
        finally:
            self.device.key_up(keycode(direction))

    def refresh_window(self) -> None:
        if self.window_provider is not None:
            self.window = self.window_provider()
