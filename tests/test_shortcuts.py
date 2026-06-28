from __future__ import annotations

import unittest

from mhscript_yjs.runtime.shortcuts import ShortcutError, normalize_shortcut, normalize_shortcut_map


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


if __name__ == "__main__":
    unittest.main()
