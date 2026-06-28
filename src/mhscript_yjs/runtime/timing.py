from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from logging import Logger


@dataclass
class Sleeper:
    logger: Logger | None = None
    random_source: random.Random = field(default_factory=random.Random)

    def delay_ms(self, milliseconds: int) -> None:
        if self.logger:
            self.logger.debug("delay_ms=%s", milliseconds)
        time.sleep(milliseconds / 1000)

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

    def delay_random_ms(self, min_ms: int, max_ms: int) -> int:
        milliseconds = self.random_source.randint(min_ms, max_ms)
        if self.logger:
            self.logger.debug("skip_delay_random_ms=%s range=%s-%s", milliseconds, min_ms, max_ms)
        return milliseconds
