from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from logging import Logger

from mhscript_yjs.runtime.control import NullRunControl, RunControl, StopRequested


@dataclass
class Sleeper:
    logger: Logger | None = None
    random_source: random.Random = field(default_factory=random.Random)
    control: RunControl = field(default_factory=NullRunControl)
    poll_interval_seconds: float = 0.05

    def delay_ms(self, milliseconds: int) -> None:
        if self.logger:
            self.logger.debug("delay_ms=%s", milliseconds)
        deadline = time.monotonic() + milliseconds / 1000
        while True:
            self.control.wait_if_paused()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(self.poll_interval_seconds, remaining))

    def delay_random_ms(self, min_ms: int, max_ms: int) -> int:
        milliseconds = self.random_source.randint(min_ms, max_ms)
        if self.logger:
            self.logger.debug("delay_random_ms=%s range=%s-%s", milliseconds, min_ms, max_ms)
        self.delay_ms(milliseconds)
        return milliseconds


class NullSleeper(Sleeper):
    def delay_ms(self, milliseconds: int) -> None:
        if self.logger:
            self.logger.debug("skip_delay_ms=%s", milliseconds)
        self.control.wait_if_paused()
        if self.control.stop_requested():
            raise StopRequested("stop requested")

    def delay_random_ms(self, min_ms: int, max_ms: int) -> int:
        milliseconds = self.random_source.randint(min_ms, max_ms)
        if self.logger:
            self.logger.debug("skip_delay_random_ms=%s range=%s-%s", milliseconds, min_ms, max_ms)
        return milliseconds
