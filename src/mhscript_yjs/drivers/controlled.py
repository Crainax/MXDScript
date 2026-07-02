from __future__ import annotations

from dataclasses import dataclass

from mhscript_yjs.drivers.base import InputDevice
from mhscript_yjs.runtime.control import (
    RunControl,
    register_input_cleanup,
    run_controlled_input,
)


@dataclass
class ControlledInputDevice:
    device: InputDevice
    control: RunControl

    def __post_init__(self) -> None:
        register_input_cleanup(self.control, self.release_all_keys)

    def open(self) -> None:
        self.device.open()

    def close(self) -> None:
        self.device.close()

    def release_all_keys(self) -> None:
        self.device.release_all_keys()

    def press_key(self, key_code: int, count: int = 1) -> None:
        run_controlled_input(self.control, lambda: self.device.press_key(key_code, count))

    def key_down(self, key_code: int) -> None:
        run_controlled_input(self.control, lambda: self.device.key_down(key_code))

    def key_up(self, key_code: int) -> None:
        run_controlled_input(self.control, lambda: self.device.key_up(key_code))

    def move_to(self, x: int, y: int, *, smooth: bool = True) -> None:
        run_controlled_input(self.control, lambda: self.device.move_to(x, y, smooth=smooth))

    def move_relative(self, dx: int, dy: int) -> None:
        run_controlled_input(self.control, lambda: self.device.move_relative(dx, dy))

    def left_click(self, count: int = 1) -> None:
        run_controlled_input(self.control, lambda: self.device.left_click(count))

    def left_down(self) -> None:
        run_controlled_input(self.control, self.device.left_down)

    def left_up(self) -> None:
        run_controlled_input(self.control, self.device.left_up)

    def mouse_wheel(self, amount: int) -> None:
        run_controlled_input(self.control, lambda: self.device.mouse_wheel(amount))
