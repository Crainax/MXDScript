from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from logging import Logger
from typing import Protocol


class StopRequested(RuntimeError):
    pass


class RunControl(Protocol):
    def wait_if_paused(self) -> None:
        ...

    def stop_requested(self) -> bool:
        ...


class NullRunControl:
    def wait_if_paused(self) -> None:
        return

    def stop_requested(self) -> bool:
        return False


@dataclass
class PauseController:
    logger: Logger | None = None
    poll_interval_seconds: float = 0.05
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _stop_event: threading.Event = field(default_factory=threading.Event)

    def pause(self) -> None:
        if not self._pause_event.is_set() and self.logger:
            self.logger.info("run_paused")
        self._pause_event.set()

    def resume(self) -> None:
        if self._pause_event.is_set() and self.logger:
            self.logger.info("run_resumed")
        self._pause_event.clear()

    def stop(self) -> None:
        if not self._stop_event.is_set() and self.logger:
            self.logger.info("run_stop_requested")
        self._stop_event.set()
        self._pause_event.clear()

    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def wait_if_paused(self) -> None:
        while self._pause_event.is_set():
            if self._stop_event.is_set():
                raise StopRequested("stop requested while paused")
            time.sleep(self.poll_interval_seconds)
        if self._stop_event.is_set():
            raise StopRequested("stop requested")
