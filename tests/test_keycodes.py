from __future__ import annotations

import unittest

from mhscript_yjs.drivers.keycodes import keycode


class KeycodeTests(unittest.TestCase):
    def test_maps_daily_script_keys(self) -> None:
        self.assertEqual(keycode("Left"), 0x25)
        self.assertEqual(keycode("PageDown"), 0x22)
        self.assertEqual(keycode("F4"), 0x73)
        self.assertEqual(keycode("LShift"), 0xA0)
        self.assertEqual(keycode("="), 0xBB)
        self.assertEqual(keycode("["), 0xDB)
        self.assertEqual(keycode("`"), 0xC0)
        self.assertEqual(keycode("x"), ord("X"))


if __name__ == "__main__":
    unittest.main()
