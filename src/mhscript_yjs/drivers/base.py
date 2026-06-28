from __future__ import annotations

from typing import Protocol


class InputDevice(Protocol):
    def open(self) -> None:
        ...

    def close(self) -> None:
        ...

    def release_all_keys(self) -> None:
        ...

    def press_key(self, key_code: int, count: int = 1) -> None:
        ...

    def move_to(self, x: int, y: int, *, smooth: bool = True) -> None:
        ...

    def left_click(self, count: int = 1) -> None:
        ...
