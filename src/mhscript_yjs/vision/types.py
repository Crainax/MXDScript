from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def from_bounds(cls, left: int, top: int, right: int, bottom: int) -> Region:
        return cls(x=left, y=top, width=right - left, height=bottom - top)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def as_mss(self) -> dict[str, int]:
        return {
            "left": self.x,
            "top": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class ImageGroup:
    name: str
    paths: tuple[Path, ...]
    threshold: float


@dataclass(frozen=True)
class MatchResult:
    group: str
    image_path: Path
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2
