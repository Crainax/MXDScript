from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.drivers.keycodes import keycode
from mhscript_yjs.runtime.timing import Sleeper


@dataclass
class CharacterActions:
    device: InputDevice
    sleeper: Sleeper
    logger: Logger

    def key_down(self, name: str) -> None:
        self.logger.debug("[CharacterAction] key_down=%s", name)
        self.device.key_down(keycode(name))

    def key_up(self, name: str) -> None:
        self.logger.debug("[CharacterAction] key_up=%s", name)
        self.device.key_up(keycode(name))

    def press(self, name: str, count: int = 1) -> None:
        self.logger.debug("[CharacterAction] key_press=%s count=%s", name, count)
        self.device.press_key(keycode(name), count)

    def delay(self, milliseconds: int) -> None:
        self.sleeper.delay_ms(max(0, int(milliseconds)))

    def delay_random(self, min_ms: int, max_ms: int) -> int:
        if min_ms > max_ms:
            min_ms, max_ms = max_ms, min_ms
        return self.sleeper.delay_random_ms(max(0, int(min_ms)), max(0, int(max_ms)))

    def hold(self, name: str, milliseconds: int) -> None:
        self.key_down(name)
        try:
            self.delay(milliseconds)
        finally:
            self.key_up(name)

    def hold_random(self, name: str, min_ms: int, max_ms: int) -> None:
        self.key_down(name)
        try:
            self.delay_random(min_ms, max_ms)
        finally:
            self.key_up(name)

    def release_all(self) -> None:
        self.logger.warning("[CharacterAction] release_all_keys")
        self.device.release_all_keys()
