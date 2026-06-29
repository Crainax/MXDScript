from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mhscript_yjs.characters.position import CharacterPosition


class Job(str, Enum):
    LYNN = "lynn"
    LARA = "lara"


@dataclass(frozen=True)
class MoveTarget:
    x: int
    y: int
    x_tolerance: int = 2
    y_tolerance: int = 0


@dataclass(frozen=True)
class MoveResult:
    reached: bool
    reason: str
    attempts: int
    last_position: CharacterPosition | None = None
