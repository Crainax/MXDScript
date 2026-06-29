from __future__ import annotations

import unittest

from mhscript_yjs.core.config import load_config
from mhscript_yjs.drivers.yjs import YjsDevice, YjsDeviceNotFoundError


class YjsDeviceTests(unittest.TestCase):
    def test_negative_result_reports_hardware_not_found(self) -> None:
        device = YjsDevice(load_config(load_local=False).yjs)

        with self.assertRaisesRegex(YjsDeviceNotFoundError, "未发现硬件"):
            device._check("M_MoveTo3", -1)  # noqa: SLF001

    def test_auto_relative_move_uses_same_machine_api(self) -> None:
        device = YjsDevice(load_config(load_local=False).yjs)

        self.assertEqual(device._choose_relative_move_api(), "M_MoveR2")  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
