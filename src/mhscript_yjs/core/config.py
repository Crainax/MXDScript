from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppSettings:
    name: str
    log_level: str
    log_dir: Path


@dataclass(frozen=True)
class MapleStorySettings:
    window_title: str
    image_root: Path


@dataclass(frozen=True)
class YjsSettings:
    dll_path: Path
    device_type: str
    open_mode: str
    port: int
    vid: int
    pid: int
    screen_width: int
    screen_height: int
    absolute_move: bool


@dataclass(frozen=True)
class TimingSettings:
    post_move_delay_ms: int
    confirm_delay_min_ms: int
    confirm_delay_max_ms: int
    poll_interval_ms: int


@dataclass(frozen=True)
class OpenPackageSettings:
    enabled: bool
    source_km: Path
    no_find_limit: int
    click_offset_x: int
    match_threshold: float
    confirm_images: tuple[str, ...]
    jing_images: tuple[str, ...]
    shi_images: tuple[str, ...]


@dataclass(frozen=True)
class ProjectConfig:
    project_root: Path
    app: AppSettings
    maple_story: MapleStorySettings
    yjs: YjsSettings
    timing: TimingSettings
    open_package: OpenPackageSettings


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | str | None = None, *, load_local: bool = True) -> ProjectConfig:
    root = project_root()
    base_path = Path(config_path) if config_path else root / "config" / "default.toml"
    if not base_path.is_absolute():
        base_path = root / base_path

    data = _read_toml(base_path)

    if load_local:
        local_path = base_path.parent / "local.toml"
        if local_path.exists():
            data = _deep_merge(data, _read_toml(local_path))

    return _to_project_config(data, root)


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _to_project_config(data: dict[str, Any], root: Path) -> ProjectConfig:
    app = data["app"]
    maple_story = data["maple_story"]
    yjs = data["yjs"]
    timing = data["timing"]
    open_package = data["scripts"]["open_package"]

    return ProjectConfig(
        project_root=root,
        app=AppSettings(
            name=str(app["name"]),
            log_level=str(app["log_level"]),
            log_dir=_path(root, app["log_dir"]),
        ),
        maple_story=MapleStorySettings(
            window_title=str(maple_story["window_title"]),
            image_root=_path(root, maple_story["image_root"]),
        ),
        yjs=YjsSettings(
            dll_path=_path(root, yjs["dll_path"]),
            device_type=str(yjs["device_type"]),
            open_mode=str(yjs["open_mode"]),
            port=int(yjs["port"]),
            vid=_int_auto(yjs["vid"]),
            pid=_int_auto(yjs["pid"]),
            screen_width=int(yjs["screen_width"]),
            screen_height=int(yjs["screen_height"]),
            absolute_move=bool(yjs["absolute_move"]),
        ),
        timing=TimingSettings(
            post_move_delay_ms=int(timing["post_move_delay_ms"]),
            confirm_delay_min_ms=int(timing["confirm_delay_min_ms"]),
            confirm_delay_max_ms=int(timing["confirm_delay_max_ms"]),
            poll_interval_ms=int(timing["poll_interval_ms"]),
        ),
        open_package=OpenPackageSettings(
            enabled=bool(open_package["enabled"]),
            source_km=_path(root, open_package["source_km"]),
            no_find_limit=int(open_package["no_find_limit"]),
            click_offset_x=int(open_package["click_offset_x"]),
            match_threshold=float(open_package.get("match_threshold", 1.0)),
            confirm_images=tuple(str(item) for item in open_package["confirm_images"]),
            jing_images=tuple(str(item) for item in open_package["jing_images"]),
            shi_images=tuple(str(item) for item in open_package["shi_images"]),
        ),
    )


def _path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def _int_auto(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(str(value), 0)
