from __future__ import annotations

import queue
import sys
import threading
from dataclasses import dataclass, field
from logging import Logger


@dataclass
class SoundPlayer:
    logger: Logger | None = None
    _queue: queue.Queue[tuple[int, int]] = field(default_factory=queue.Queue)
    _worker: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def beep(self, frequency: int = 800, duration_ms: int = 120) -> None:
        frequency = max(37, min(32767, int(frequency)))
        duration_ms = max(1, min(5000, int(duration_ms)))
        self._ensure_worker()
        self._queue.put((frequency, duration_ms))
        if self.logger:
            self.logger.info("beep queued frequency=%s duration_ms=%s", frequency, duration_ms)

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._run, name="mxdscript-sound", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            frequency, duration_ms = self._queue.get()
            try:
                _play_sound(frequency, duration_ms)
            except Exception as exc:  # pragma: no cover - depends on OS sound device.
                if self.logger:
                    self.logger.warning("beep failed: %s", exc)


def _play_sound(frequency: int, duration_ms: int) -> None:
    if sys.platform == "win32":
        import winsound

        try:
            winsound.Beep(frequency, duration_ms)
            return
        except RuntimeError:
            winsound.MessageBeep(winsound.MB_OK)
            return
    print("\a", end="", flush=True)


_default_player = SoundPlayer()


def beep(frequency: int = 800, duration_ms: int = 120, *, logger: Logger | None = None) -> None:
    if logger is not None:
        _default_player.logger = logger
    _default_player.beep(frequency, duration_ms)
