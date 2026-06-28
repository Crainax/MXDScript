from __future__ import annotations

import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


IMPORTANT_LEVEL = 25
logging.addLevelName(IMPORTANT_LEVEL, "IMPORTANT")


def setup_script_logger(
    *,
    script_name: str,
    log_dir: Path,
    level: str = "INFO",
    console: bool = True,
) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"mhscript_yjs.{script_name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    close_logger_handlers(logger)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = log_dir / f"{script_name}_{timestamp}.log"
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
        stream_handler.setLevel(_level(level))
        logger.addHandler(stream_handler)

    logger.info("log_file=%s", log_path)
    return logger


def log_important(logger: logging.Logger, message: str, *args: object, **kwargs: object) -> None:
    logger.log(IMPORTANT_LEVEL, message, *args, **kwargs)


def close_logger_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def logger_file_path(logger: logging.Logger) -> Path | None:
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler):
            return Path(handler.baseFilename)
    return None


def _level(value: str) -> int:
    return getattr(logging, value.upper(), logging.INFO)
