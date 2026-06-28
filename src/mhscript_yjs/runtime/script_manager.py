from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any

from mhscript_yjs.core.config import load_config
from mhscript_yjs.runtime.app_paths import logs_dir
from mhscript_yjs.runtime.control import PauseController
from mhscript_yjs.runtime.logging import (
    close_logger_handlers,
    logger_file_path,
    setup_script_logger,
)
from mhscript_yjs.runtime.mouse_settings import MousePointerPrecisionManager
from mhscript_yjs.scripts.registry import ScriptDefinition, ScriptRunContext, get_script_definitions


Event = dict[str, Any]


class ScriptEventHandler(logging.Handler):
    def __init__(self, events: queue.Queue[Event], script_id: str) -> None:
        super().__init__(level=logging.INFO)
        self.events = events
        self.script_id = script_id

    def emit(self, record: logging.LogRecord) -> None:
        self.events.put(
            {
                "type": "log",
                "scriptId": self.script_id,
                "level": record.levelname,
                "message": self.format(record),
            }
        )


class ScriptManager:
    def __init__(self, definitions: tuple[ScriptDefinition, ...] | None = None) -> None:
        self.definitions = definitions or get_script_definitions()
        self._definitions_by_id = {definition.id: definition for definition in self.definitions}
        self._lock = threading.RLock()
        self._events: queue.Queue[Event] = queue.Queue()
        self._states = {definition.id: "idle" for definition in self.definitions}
        self._log_paths: dict[str, Path] = {}
        self._last_results: dict[str, dict[str, Any]] = {}
        self._active_script_id: str | None = None
        self._controller: PauseController | None = None
        self._worker: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "scripts": [self._script_snapshot(definition) for definition in self.definitions],
                "activeScriptId": self._active_script_id,
                "logDir": str(logs_dir()),
            }

    def poll_events(self) -> list[Event]:
        events: list[Event] = []
        try:
            while True:
                events.append(self._events.get_nowait())
        except queue.Empty:
            return events

    def start(
        self,
        script_id: str,
        *,
        dry_run: bool = False,
        skip_delays: bool = False,
    ) -> dict[str, Any]:
        definition = self._require_definition(script_id)
        with self._lock:
            if self._active_script_id is not None:
                raise RuntimeError("已有脚本正在运行，请先暂停或停止当前脚本。")

            controller = PauseController()
            self._controller = controller
            self._active_script_id = script_id
            self._states[script_id] = "running"
            self._last_results.pop(script_id, None)

            worker = threading.Thread(
                target=self._run_script,
                args=(definition, controller, dry_run, skip_delays),
                name=f"mxdscript-{script_id}",
                daemon=True,
            )
            self._worker = worker
            worker.start()

        self._emit_state(script_id, "running")
        return self.snapshot()

    def pause(self) -> dict[str, Any]:
        with self._lock:
            if self._active_script_id is None or self._controller is None:
                return self.snapshot()
            if self._states[self._active_script_id] != "running":
                return self.snapshot()
            self._controller.pause()
            self._states[self._active_script_id] = "paused"
            script_id = self._active_script_id

        self._emit_state(script_id, "paused")
        return self.snapshot()

    def resume(self) -> dict[str, Any]:
        with self._lock:
            if self._active_script_id is None or self._controller is None:
                return self.snapshot()
            if self._states[self._active_script_id] != "paused":
                return self.snapshot()
            self._controller.resume()
            self._states[self._active_script_id] = "running"
            script_id = self._active_script_id

        self._emit_state(script_id, "running")
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._active_script_id is None or self._controller is None:
                return self.snapshot()
            script_id = self._active_script_id
            if self._states[script_id] not in {"running", "paused"}:
                return self.snapshot()
            self._controller.stop()
            self._states[script_id] = "stopping"

        self._emit_state(script_id, "stopping")
        return self.snapshot()

    def _run_script(
        self,
        definition: ScriptDefinition,
        controller: PauseController,
        dry_run: bool,
        skip_delays: bool,
    ) -> None:
        script_id = definition.id
        logger: logging.Logger | None = None
        mouse_precision: MousePointerPrecisionManager | None = None
        try:
            config = load_config()
            logger = setup_script_logger(
                script_name=script_id,
                log_dir=logs_dir(),
                level=config.app.log_level,
                console=False,
            )
            log_path = logger_file_path(logger)
            if log_path is not None:
                self._log_paths[script_id] = log_path
                self._events.put(
                    {
                        "type": "state",
                        "scriptId": script_id,
                        "state": self._states[script_id],
                        "logPath": str(log_path),
                    }
                )

            event_handler = ScriptEventHandler(self._events, script_id)
            event_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
            )
            logger.addHandler(event_handler)
            controller.logger = logger

            logger.info(
                "脚本启动：%s，模式=%s，模块=%s。",
                definition.name,
                "dry-run" if dry_run else "live",
                definition.module,
            )

            if definition.requires_mouse_precision and not dry_run:
                mouse_precision = MousePointerPrecisionManager(logger=logger)
                mouse_precision.disable_temporarily()

            context = ScriptRunContext(
                config=config,
                logger=logger,
                control=controller,
                dry_run=dry_run,
                skip_delays=skip_delays,
            )
            result = definition.runner(context)
            payload = {
                "exitReason": result.exit_reason,
                "iterations": result.iterations,
                "details": dict(result.details),
            }
            logger.info(
                "脚本结束：%s，原因=%s，循环次数=%s。",
                definition.name,
                result.exit_reason,
                result.iterations,
            )
            with self._lock:
                self._last_results[script_id] = payload
                self._states[script_id] = "finished"
                self._active_script_id = None
                self._controller = None
                self._worker = None
            self._events.put({"type": "finished", "scriptId": script_id, "result": payload})
            self._emit_state(script_id, "finished")
        except Exception as exc:
            if logger is not None:
                logger.exception("脚本异常退出：%s", exc)
            with self._lock:
                self._last_results[script_id] = {
                    "exitReason": "error",
                    "error": f"{exc.__class__.__name__}: {exc}",
                }
                self._states[script_id] = "error"
                self._active_script_id = None
                self._controller = None
                self._worker = None
            self._events.put(
                {
                    "type": "error",
                    "scriptId": script_id,
                    "message": f"{exc.__class__.__name__}: {exc}",
                }
            )
            self._emit_state(script_id, "error")
        finally:
            if mouse_precision is not None:
                try:
                    mouse_precision.restore()
                except Exception as exc:
                    self._events.put(
                        {
                            "type": "error",
                            "scriptId": script_id,
                            "message": f"恢复鼠标指针精度失败：{exc}",
                        }
                    )
            if logger is not None:
                close_logger_handlers(logger)

    def _script_snapshot(self, definition: ScriptDefinition) -> dict[str, Any]:
        return {
            "id": definition.id,
            "name": definition.name,
            "category": definition.category,
            "description": definition.description,
            "module": definition.module,
            "defaultShortcut": definition.default_shortcut,
            "placeholder": definition.placeholder,
            "requiresMousePrecision": definition.requires_mouse_precision,
            "status": self._states[definition.id],
            "logPath": str(self._log_paths[definition.id])
            if definition.id in self._log_paths
            else None,
            "lastResult": self._last_results.get(definition.id),
        }

    def _emit_state(self, script_id: str, state: str) -> None:
        self._events.put({"type": "state", "scriptId": script_id, "state": state})

    def _require_definition(self, script_id: str) -> ScriptDefinition:
        try:
            return self._definitions_by_id[script_id]
        except KeyError as exc:
            raise ValueError(f"未知脚本：{script_id}") from exc
