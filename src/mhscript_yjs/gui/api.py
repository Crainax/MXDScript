from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

from mhscript_yjs import __version__
from mhscript_yjs.runtime.app_paths import logs_dir, settings_path
from mhscript_yjs.runtime.global_hotkeys import GlobalHotkeyService, HotkeyBinding
from mhscript_yjs.runtime.script_manager import ScriptManager
from mhscript_yjs.runtime.shortcuts import (
    ShortcutError,
    normalize_shortcut_map,
    shortcut_to_win_hotkey,
)


class GuiApi:
    def __init__(
        self,
        manager: ScriptManager | None = None,
        *,
        enable_hotkeys: bool = False,
    ) -> None:
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
        settings = self._load_settings()
        return self._call(
            lambda: self.manager.start(
                script_id,
                dry_run=bool(options.get("dryRun", False)),
                skip_delays=bool(options.get("skipDelays", False)),
                script_options=_script_options_for(settings, script_id),
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

    def save_script_options(self, script_id: str, options: dict[str, Any]) -> dict[str, Any]:
        settings = self._load_settings()
        defaults = _default_script_options(self.manager)
        if script_id not in defaults:
            return {"ok": False, "error": f"Unknown configurable script: {script_id}"}

        current = settings.get("scriptOptions")
        if not isinstance(current, dict):
            current = {}

        merged = dict(defaults[script_id])
        existing = current.get(script_id)
        if isinstance(existing, dict):
            for key, value in existing.items():
                if key in merged:
                    merged[key] = _coerce_script_option(key, merged[key], value)
        for key, value in options.items():
            if key in merged:
                merged[key] = _coerce_script_option(key, merged[key], value)

        current[script_id] = merged
        settings["scriptOptions"] = _normalize_script_options(self.manager, current)
        _write_json(settings_path(), settings)
        return {"ok": True, "settings": settings}

    def open_log_dir(self) -> dict[str, Any]:
        return self._call(lambda: _open_path(logs_dir()))

    def open_path(self, path: str) -> dict[str, Any]:
        return self._call(lambda: _open_path(Path(path)))

    def select_directory(self, initial_path: str = "") -> dict[str, Any]:
        try:
            return {"ok": True, "path": str(_select_directory(initial_path))}
        except Exception as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}

    def _call(self, callback: Any) -> dict[str, Any]:
        try:
            result = callback()
            return {"ok": True, "runtime": result}
        except Exception as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}

    def _load_settings(self) -> dict[str, Any]:
        defaults = {
            "shortcuts": _default_shortcuts(self.manager),
            "scriptOptions": _default_script_options(self.manager),
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
            with suppress(ShortcutError):
                defaults["shortcuts"].update(
                    normalize_shortcut_map(
                        (definition.id for definition in self.manager.definitions),
                        {str(key): str(value) for key, value in shortcuts.items()},
                    )
                )
        if defaults["shortcuts"].get("open_package") == "F10":
            defaults["shortcuts"]["open_package"] = ""
        _clear_duplicate_shortcuts(self.manager, defaults["shortcuts"])
        theme = data.get("theme")
        if theme in {"system", "light", "dark"}:
            defaults["theme"] = theme
        script_options = data.get("scriptOptions")
        if isinstance(script_options, dict):
            defaults["scriptOptions"] = _normalize_script_options(self.manager, script_options)
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
            if not shortcut.strip():
                continue
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
                    script_options=_script_options_for(settings, script_id),
                )
                return

            if active_script_id != script_id:
                self.manager.start(
                    script_id,
                    dry_run=bool(settings.get("dryRun", False)),
                    skip_delays=bool(settings.get("skipDelays", False)),
                    script_options=_script_options_for(settings, script_id),
                )
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


def _clear_duplicate_shortcuts(manager: ScriptManager, shortcuts: dict[str, str]) -> None:
    used: set[str] = set()
    for definition in manager.definitions:
        shortcut = shortcuts.get(definition.id, "")
        if not shortcut:
            continue
        if shortcut in used:
            shortcuts[definition.id] = ""
            continue
        used.add(shortcut)


def _default_script_options(manager: ScriptManager) -> dict[str, dict[str, Any]]:
    return {
        definition.id: {
            str(key): _default_script_option_value(value)
            for key, value in definition.default_options.items()
        }
        for definition in manager.definitions
        if definition.default_options
    }


def _normalize_script_options(
    manager: ScriptManager,
    raw_options: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    normalized = _default_script_options(manager)
    for script_id, options in raw_options.items():
        if script_id not in normalized or not isinstance(options, dict):
            continue
        merged = dict(normalized[script_id])
        for key, value in options.items():
            if key in merged:
                merged[key] = _coerce_script_option(key, merged[key], value)
        normalized[script_id] = merged
    return normalized


def _default_script_option_value(value: Any) -> Any:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _coerce_script_option(key: str, default_value: Any, value: Any) -> Any:
    if isinstance(default_value, bool):
        return bool(value)
    if isinstance(default_value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(default_value)
        if not math.isfinite(number):
            return float(default_value)
        if key == "intervalSeconds":
            return max(0.05, min(10.0, number))
        if 0.0 <= float(default_value) <= 1.0:
            return max(0.0, min(1.0, number))
        return number
    if isinstance(default_value, str):
        return str(value)
    return value


def _script_options_for(settings: dict[str, Any], script_id: str) -> dict[str, Any]:
    script_options = settings.get("scriptOptions")
    if not isinstance(script_options, dict):
        return {}
    options = script_options.get(script_id)
    return dict(options) if isinstance(options, dict) else {}


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


def _select_directory(initial_path: str) -> Path:
    import tkinter as tk
    from tkinter import filedialog

    cleaned = initial_path.strip().strip('"').strip("'")
    initial = Path(cleaned) if cleaned else Path.cwd()
    if not initial.exists():
        initial = initial.parent if initial.parent.exists() else Path.cwd()

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(initialdir=str(initial), mustexist=False)
    finally:
        root.destroy()
    if selected:
        return Path(selected).resolve()
    if cleaned:
        return Path(cleaned).resolve()
    return initial.resolve()


def _package_version() -> str:
    return __version__
