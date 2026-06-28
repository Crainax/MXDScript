from __future__ import annotations

import unittest

from mhscript_yjs.core.config import load_config


class ConfigTests(unittest.TestCase):
    def test_load_default_config(self) -> None:
        config = load_config(load_local=False)

        self.assertEqual(config.app.log_level, "INFO")
        self.assertEqual(config.maple_story.window_title, "MapleStory")
        self.assertEqual(config.yjs.port, 1)
        self.assertEqual(config.yjs.vid, 0xC216)
        self.assertEqual(config.yjs.pid, 0x0301)
        self.assertEqual(config.yjs.move_api, "auto")
        self.assertEqual(config.open_package.match_threshold, 1.0)
        self.assertEqual(config.open_package.confirm_match_threshold, 0.93)
        self.assertEqual(config.open_package.event_match_threshold, 0.99)
        self.assertEqual(
            config.open_package.confirm_images,
            ("UI\\Yes.bmp", "UI\\OK.bmp", "UI\\OK2.bmp"),
        )


if __name__ == "__main__":
    unittest.main()
