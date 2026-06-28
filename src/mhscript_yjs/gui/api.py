from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mhscript_yjs import __version__
from mhscript_yjs.runtime.app_paths import logs_dir, settings_path
from mhscript_yjs.runtime.global_hotkeys import GlobalHotkeyService, HotkeyBinding
from mhscript_yjs.runtime.script_manager import ScriptManager
from mhscript_yjs.runtime.shortcuts import ShortcutError, normalize_shortcut_map, shortcut_to_win_hotkey


class GuiApi:
    def __init__(self, manager: ScriptManager | None = None, *, enable_hotkeys: bool = False) -> None:
        self.manager = manager or ScriptManager()
        self._hotkeys: GlobalHotkeyService | None = (
            GlobalHotkeyService() if enable_hotkeys else None
        )
        if self._hotkeys is not None:
            self._refresh_hotkeys(self._load_settings())

    def get_state(self) -> dict[str, Any]:
        return {
            "ok": True,
            "app": {
                "title": "MXD脚本库",
                "version": _package_version(),
                "logDir": str(logs_dir()),
            },
            "runtime": self.manager.snapshot(),
            "settings": self._load_settings(),
        }

    def poll_events(self) -> dict[str, Any]:
        return {"ok": True, "events": self.manager.poll_events()}

    def start_script(self, script_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        return self._call(
            lambda: self.manager.start(
                script_id,
                dry_run=bool(options.get("dryRun", False)),
                skip_delays=bool(options.get("skipDelays", False)),
            )
        )

    def pause_script(self) -> dict[str, Any]:
        return self._call(self.manager.pause)

    def resume_script(self) -> dict[str, Any]:
        return self._call(self.manager.resume)

    def stop_script(self) -> dict[str, Any]:
        return self._call(self.manager.stop)

    def save_shortcuts(self, shortcuts: dict[str, str]) -> dict[str, Any]:
        try:
            definitions = self.manager.definitions
            normalized = normalize_shortcut_map(
                (definition.id for definition in definitions),
                shortcuts,
            )
            merged = {
                definition.id: normalized.get(definition.id, definition.default_shortcut)
                for definition in definitions
            }
            settings = self._load_settings()
            settings["shortcuts"] = merged
            _write_json(settings_path(), settings)
            self._refresh_hotkeys(settings)
            return {"ok": True, "settings": settings}
        except ShortcutError as exc:
            return {"ok": False, "error": str(exc)}

    def save_run_options(self, *, dry_run: bool, skip_delays: bool) -> dict[str, Any]:
        settings = self._load_settings()
        settings["dryRun"] = bool(dry_run)
        settings["skipDelays"] = bool(skip_delays)
        _write_json(settings_path(), settings)
        return {"ok": True, "settings": settings}

    def open_log_dir(self) -> dict[str, Any]:
        return self._call(lambda: _open_path(logs_dir()))

    def open_path(self, path: str) -> dict[str, Any]:
        return self._call(lambda: _open_path(Path(path)))

    def _call(self, callback: Any) -> dict[str, Any]:
        try:
            result = callback()
            return {"ok": True, "runtime": result}
        except Exception as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}

    def _load_settings(self) -> dict[str, Any]:
        defaults = {
            "shortcuts": _default_shortcuts(self.manager),
            "theme": "system",
            "dryRun": False,
            "skipDelays": False,
        }
        path = settings_path()
        if not path.exists():
            _write_json(path, defaults)
            return defaults

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _write_json(path, defaults)
            return defaults

        shortcuts = data.get("shortcuts")
        if isinstance(shortcuts, dict):
            try:
                defaults["shortcuts"].update(
                    normalize_shortcut_map(
                        (definition.id for definition in self.manager.definitions),
                        {str(key): str(value) for key, value in shortcuts.items()},
                    )
                )
            except ShortcutError:
                pass
        theme = data.get("theme")
        if theme in {"system", "light", "dark"}:
            defaults["theme"] = theme
        defaults["dryRun"] = bool(data.get("dryRun", defaults["dryRun"]))
        defaults["skipDelays"] = bool(data.get("skipDelays", defaults["skipDelays"]))
        return defaults

    def _refresh_hotkeys(self, settings: dict[str, Any]) -> None:
        if self._hotkeys is None:
            return

        bindings: list[HotkeyBinding] = []
        shortcuts = settings.get("shortcuts", {})
        if not isinstance(shortcuts, dict):
            return

        for definition in self.manager.definitions:
            shortcut = str(shortcuts.get(definition.id, definition.default_shortcut))
            try:
                hotkey = shortcut_to_win_hotkey(shortcut)
            except ShortcutError:
                continue
            bindings.append(
                HotkeyBinding(
                    name=definition.name,
                    shortcut=hotkey.shortcut,
                    modifiers=hotkey.modifiers,
                    vk=hotkey.vk,
                    callback=lambda script_id=definition.id: self._activate_shortcut(script_id),
                )
            )
        self._hotkeys.replace_bindings(bindings)

    def _activate_shortcut(self, script_id: str) -> None:
        try:
            settings = self._load_settings()
            snapshot = self.manager.snapshot()
            active_script_id = snapshot.get("activeScriptId")

            if active_script_id is None:
                self.manager.start(
                    script_id,
                    dry_run=bool(settings.get("dryRun", False)),
                    skip_delays=bool(settings.get("skipDelays", False)),
                )
                return

            if active_script_id != script_id:
                return

            script = next(
                (
                    item
                    for item in snapshot.get("scripts", [])
                    if isinstance(item, dict) and item.get("id") == script_id
                ),
                None,
            )
            status = script.get("status") if isinstance(script, dict) else None
            if status == "running":
                self.manager.pause()
            elif status == "paused":
                self.manager.resume()
        except Exception as exc:
            self.manager.emit_error(script_id, f"快捷键执行失败：{exc.__class__.__name__}: {exc}")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_shortcuts(manager: ScriptManager) -> dict[str, str]:
    return {definition.id: definition.default_shortcut for definition in manager.definitions}


def _open_path(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
    return {"path": str(path)}


def _package_version() -> str:
    return __version__
