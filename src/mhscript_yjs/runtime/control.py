from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from logging import Logger
from typing import Protocol, TypeVar


T = TypeVar("T")


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
    _input_lock: threading.RLock = field(default_factory=threading.RLock)
    _cleanup_lock: threading.RLock = field(default_factory=threading.RLock)
    _input_cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list)

    def add_input_cleanup(self, callback: Callable[[], None]) -> None:
        with self._cleanup_lock:
            self._input_cleanup_callbacks.append(callback)
            should_run_now = self._pause_event.is_set() or self._stop_event.is_set()
        if should_run_now:
            self._run_input_cleanup_callback(callback, reason="register")

    def pause(self) -> None:
        should_cleanup = False
        with self._input_lock:
            if not self._pause_event.is_set():
                if self.logger:
                    self.logger.info("run_paused")
                should_cleanup = True
            self._pause_event.set()
        if should_cleanup:
            self._run_input_cleanup(reason="pause")

    def resume(self) -> None:
        if self._pause_event.is_set() and self.logger:
            self.logger.info("run_resumed")
        self._pause_event.clear()

    def stop(self) -> None:
        should_cleanup = False
        with self._input_lock:
            if not self._stop_event.is_set():
                if self.logger:
                    self.logger.info("run_stop_requested")
                should_cleanup = True
            self._stop_event.set()
            self._pause_event.clear()
        if should_cleanup:
            self._run_input_cleanup(reason="stop")

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

    def run_input(self, action: Callable[[], T]) -> T:
        while True:
            self.wait_if_paused()
            with self._input_lock:
                if self._pause_event.is_set():
                    continue
                if self._stop_event.is_set():
                    raise StopRequested("stop requested")
                return action()

    def _run_input_cleanup(self, *, reason: str) -> None:
        with self._cleanup_lock:
            callbacks = tuple(self._input_cleanup_callbacks)
        for callback in callbacks:
            self._run_input_cleanup_callback(callback, reason=reason)

    def _run_input_cleanup_callback(self, callback: Callable[[], None], *, reason: str) -> None:
        try:
            callback()
        except Exception as exc:
            if self.logger:
                self.logger.warning("input_cleanup_failed reason=%s error=%r", reason, exc)


def register_input_cleanup(control: RunControl, callback: Callable[[], None]) -> None:
    if isinstance(control, PauseController):
        control.add_input_cleanup(callback)


def run_controlled_input(control: RunControl, action: Callable[[], T]) -> T:
    if isinstance(control, PauseController):
        return control.run_input(action)
    control.wait_if_paused()
    if control.stop_requested():
        raise StopRequested("stop requested")
    return action()
