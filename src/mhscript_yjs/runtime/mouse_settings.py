from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from logging import Logger


class MouseSettingsError(RuntimeError):
    pass


@dataclass(frozen=True)
class MouseAccelerationSettings:
    threshold1: int
    threshold2: int
    speed: int

    @classmethod
    def disabled(cls) -> MouseAccelerationSettings:
        return cls(threshold1=0, threshold2=0, speed=0)

    @property
    def enhance_pointer_precision_enabled(self) -> bool:
        return self.speed != 0


class MousePointerPrecisionManager:
    def __init__(self, *, logger: Logger | None = None) -> None:
        self.logger = logger
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SystemParametersInfoW.restype = wintypes.BOOL
        self._user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        self._saved: MouseAccelerationSettings | None = None
        self._disabled = False

    @property
    def saved(self) -> MouseAccelerationSettings | None:
        return self._saved

    def disable_temporarily(self) -> None:
        if self._saved is None:
            self._saved = self.get()
            if self.logger:
                self.logger.info("mouse_precision_saved %s", self._saved)

        target = MouseAccelerationSettings.disabled()
        current = self.get()
        if current == target:
            self._disabled = True
            if self.logger:
                self.logger.info("mouse_precision_already_disabled")
            return

        self.set(target)
        self._disabled = True
        if self.logger:
            self.logger.info("mouse_precision_disabled")

    def restore(self) -> None:
        if self._saved is None:
            return
        current = self.get()
        if current == self._saved:
            self._disabled = False
            if self.logger:
                self.logger.info("mouse_precision_already_restored")
            return

        self.set(self._saved)
        self._disabled = False
        if self.logger:
            self.logger.info("mouse_precision_restored %s", self._saved)

    def get(self) -> MouseAccelerationSettings:
        values = (ctypes.c_int * 3)()
        self._system_parameters_info(SPI_GETMOUSE, 0, ctypes.byref(values), 0)
        return MouseAccelerationSettings(values[0], values[1], values[2])

    def set(self, settings: MouseAccelerationSettings) -> None:
        values = (ctypes.c_int * 3)(
            settings.threshold1,
            settings.threshold2,
            settings.speed,
        )
        self._system_parameters_info(
            SPI_SETMOUSE,
            0,
            ctypes.byref(values),
            SPIF_UPDATEINIFILE | SPIF_SENDCHANGE,
        )

    def _system_parameters_info(
        self,
        action: int,
        ui_param: int,
        value: object,
        flags: int,
    ) -> None:
        if not self._user32.SystemParametersInfoW(action, ui_param, value, flags):
            raise MouseSettingsError(f"SystemParametersInfoW failed action={action}")


SPI_GETMOUSE = 0x0003
SPI_SETMOUSE = 0x0004
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002
