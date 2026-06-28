from __future__ import annotations

from dataclasses import dataclass, field
from logging import Logger


@dataclass
class RecordedAction:
    name: str
    args: tuple[object, ...] = ()


@dataclass
class DryRunDevice:
    logger: Logger | None = None
    actions: list[RecordedAction] = field(default_factory=list)
    is_open: bool = False

    def open(self) -> None:
        self.is_open = True
        self._record("open")

    def close(self) -> None:
        self._record("close")
        self.is_open = False

    def release_all_keys(self) -> None:
        self._record("release_all_keys")

    def press_key(self, key_code: int, count: int = 1) -> None:
        self._record("press_key", key_code, count)

    def move_to(self, x: int, y: int, *, smooth: bool = True) -> None:
        self._record("move_to", x, y, smooth)

    def left_click(self, count: int = 1) -> None:
        self._record("left_click", count)

    def _record(self, name: str, *args: object) -> None:
        action = RecordedAction(name=name, args=args)
        self.actions.append(action)
        if self.logger:
            self.logger.info("dry_run_action=%s args=%s", name, args)
