from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ShortcutError(ValueError):
    pass


@dataclass(frozen=True)
class ShortcutConflict:
    shortcut: str
    first_script_id: str
    second_script_id: str


MODIFIER_ORDER = ("Ctrl", "Alt", "Shift")
MODIFIER_ALIASES = {
    "CTRL": "Ctrl",
    "CONTROL": "Ctrl",
    "ALT": "Alt",
    "SHIFT": "Shift",
}


def normalize_shortcut(value: str) -> str:
    parts = [part.strip() for part in value.replace("-", "+").split("+") if part.strip()]
    if not parts:
        raise ShortcutError("快捷键不能为空")

    modifiers: set[str] = set()
    key: str | None = None
    for part in parts:
        upper = part.upper()
        if upper in MODIFIER_ALIASES:
            modifiers.add(MODIFIER_ALIASES[upper])
            continue
        if key is not None:
            raise ShortcutError("快捷键只能包含一个主键")
        key = _normalize_key(part)

    if key is None:
        raise ShortcutError("快捷键缺少主键")
    if key == "Esc":
        raise ShortcutError("Esc 是暂停/停止快捷键，不能作为启动快捷键")

    if _is_function_key(key):
        ordered = [modifier for modifier in MODIFIER_ORDER if modifier in modifiers]
        return "+".join([*ordered, key])

    if not modifiers:
        raise ShortcutError("字母或数字快捷键需要搭配 Ctrl、Alt 或 Shift")

    ordered = [modifier for modifier in MODIFIER_ORDER if modifier in modifiers]
    return "+".join([*ordered, key])


def normalize_shortcut_map(script_ids: Iterable[str], shortcuts: dict[str, str]) -> dict[str, str]:
    allowed = set(script_ids)
    normalized: dict[str, str] = {}
    used: dict[str, str] = {}

    for script_id, shortcut in shortcuts.items():
        if script_id not in allowed:
            continue
        normalized_shortcut = normalize_shortcut(shortcut)
        owner = used.get(normalized_shortcut)
        if owner is not None:
            raise ShortcutError(f"快捷键 {normalized_shortcut} 已被 {owner} 使用")
        used[normalized_shortcut] = script_id
        normalized[script_id] = normalized_shortcut

    return normalized


def _normalize_key(value: str) -> str:
    upper = value.upper()
    if upper in {"ESC", "ESCAPE"}:
        return "Esc"
    if upper.startswith("F") and upper[1:].isdigit():
        number = int(upper[1:])
        if 1 <= number <= 12:
            return f"F{number}"
    if len(value) == 1 and value.isalnum():
        return value.upper()
    raise ShortcutError(f"不支持的快捷键主键：{value}")


def _is_function_key(value: str) -> bool:
    return value.startswith("F") and value[1:].isdigit() and 1 <= int(value[1:]) <= 12
