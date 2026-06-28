from __future__ import annotations

import logging
import shutil
from pathlib import Path

from mhscript_yjs.core.config import external_root, project_root


def prepare_runtime_resources(logger: logging.Logger | None = None) -> None:
    bundled_root = project_root()
    writable_root = external_root()

    for relative in (Path("assets"), Path("config"), Path("vendor") / "msdk"):
        (writable_root / relative).mkdir(parents=True, exist_ok=True)

    _copy_tree_missing(bundled_root / "assets", writable_root / "assets", logger=logger)
    _copy_file_missing(
        bundled_root / "config" / "default.toml",
        writable_root / "config" / "default.toml",
        logger=logger,
    )
    _copy_file_missing(
        bundled_root / "vendor" / "msdk" / "msdk.dll",
        writable_root / "vendor" / "msdk" / "msdk.dll",
        logger=logger,
    )


def _copy_tree_missing(source: Path, target: Path, *, logger: logging.Logger | None) -> None:
    if _same_path(source, target) or not source.exists():
        return

    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        if logger:
            logger.debug("resource_copied source=%s target=%s", item, destination)


def _copy_file_missing(source: Path, target: Path, *, logger: logging.Logger | None) -> None:
    if _same_path(source, target) or not source.is_file() or target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if logger:
        logger.debug("resource_copied source=%s target=%s", source, target)


def _same_path(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return False
