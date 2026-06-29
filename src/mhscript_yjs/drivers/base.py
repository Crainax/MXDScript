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

    def key_down(self, key_code: int) -> None:
        ...

    def key_up(self, key_code: int) -> None:
        ...

    def move_to(self, x: int, y: int, *, smooth: bool = True) -> None:
        ...

    def move_relative(self, dx: int, dy: int) -> None:
        ...

    def left_click(self, count: int = 1) -> None:
        ...

    def left_down(self) -> None:
        ...

    def left_up(self) -> None:
        ...

    def mouse_wheel(self, amount: int) -> None:
        ...
