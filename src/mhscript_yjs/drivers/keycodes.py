VK_ENTER = 13


KEYCODES: dict[str, int] = {
    "enter": VK_ENTER,
    "return": VK_ENTER,
    "esc": 27,
    "escape": 27,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "pagedown": 0x22,
    "page_down": 0x22,
    "space": 32,
    "tab": 9,
    "alt": 0x12,
    "lalt": 0xA4,
    "shift": 0x10,
    "lshift": 0xA0,
    "`": 0xC0,
    "=": 0xBB,
    "[": 0xDB,
}
KEYCODES.update({f"f{number}": 0x70 + number - 1 for number in range(1, 13)})
KEYCODES.update({str(number): ord(str(number)) for number in range(10)})
KEYCODES.update({chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)})


def keycode(name: str) -> int:
    normalized = name.strip().lower()
    if normalized not in KEYCODES:
        raise KeyError(f"Unknown key name: {name}")
    return KEYCODES[normalized]
