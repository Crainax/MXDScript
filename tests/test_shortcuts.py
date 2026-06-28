from __future__ import annotations

import unittest

from mhscript_yjs.runtime.shortcuts import (
    WIN32_MOD_NOREPEAT,
    ShortcutError,
    normalize_shortcut,
    normalize_shortcut_map,
    shortcut_to_win_hotkey,
)


class ShortcutTests(unittest.TestCase):
    def test_allows_function_key_without_modifiers(self) -> None:
        self.assertEqual(normalize_shortcut("f10"), "F10")

    def test_allows_modified_letter_key(self) -> None:
        self.assertEqual(normalize_shortcut("alt+ctrl+o"), "Ctrl+Alt+O")

    def test_rejects_escape(self) -> None:
        with self.assertRaises(ShortcutError):
            normalize_shortcut("Esc")

    def test_rejects_bare_letter(self) -> None:
        with self.assertRaises(ShortcutError):
            normalize_shortcut("A")

    def test_rejects_duplicate_shortcuts(self) -> None:
        with self.assertRaises(ShortcutError):
            normalize_shortcut_map(("first", "second"), {"first": "F8", "second": "f8"})

    def test_converts_function_key_to_windows_hotkey(self) -> None:
        hotkey = shortcut_to_win_hotkey("F10")

        self.assertEqual(hotkey.shortcut, "F10")
        self.assertEqual(hotkey.modifiers, WIN32_MOD_NOREPEAT)
        self.assertEqual(hotkey.vk, 0x79)

    def test_converts_modified_letter_to_windows_hotkey(self) -> None:
        hotkey = shortcut_to_win_hotkey("ctrl+alt+o")

        self.assertEqual(hotkey.shortcut, "Ctrl+Alt+O")
        self.assertEqual(hotkey.modifiers, WIN32_MOD_NOREPEAT | 0x0001 | 0x0002)
        self.assertEqual(hotkey.vk, ord("O"))


if __name__ == "__main__":
    unittest.main()
