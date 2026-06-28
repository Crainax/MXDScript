VK_ENTER = 13


KEYCODES: dict[str, int] = {
    "enter": VK_ENTER,
    "return": VK_ENTER,
    "esc": 27,
    "escape": 27,
    "space": 32,
    "tab": 9,
}


def keycode(name: str) -> int:
    normalized = name.strip().lower()
    if normalized not in KEYCODES:
        raise KeyError(f"Unknown key name: {name}")
    return KEYCODES[normalized]
