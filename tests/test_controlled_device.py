from __future__ import annotations

import threading
import time
import unittest

from mhscript_yjs.drivers.controlled import ControlledInputDevice
from mhscript_yjs.drivers.dry_run import DryRunDevice
from mhscript_yjs.runtime.control import PauseController


class ControlledInputDeviceTests(unittest.TestCase):
    def test_pause_releases_keys_and_blocks_new_input_until_resume(self) -> None:
        control = PauseController(poll_interval_seconds=0.01)
        raw_device = DryRunDevice()
        device = ControlledInputDevice(raw_device, control)

        device.open()
        device.key_down(65)
        control.pause()

        self.assertEqual(
            [action.name for action in raw_device.actions],
            ["open", "key_down", "release_all_keys"],
        )

        finished = threading.Event()
        worker = threading.Thread(target=lambda: (device.key_down(66), finished.set()))
        worker.start()
        time.sleep(0.05)

        self.assertFalse(finished.is_set())
        self.assertEqual(
            [action.name for action in raw_device.actions],
            ["open", "key_down", "release_all_keys"],
        )

        control.resume()
        worker.join(timeout=1)

        self.assertTrue(finished.is_set())
        self.assertEqual(
            [action.name for action in raw_device.actions],
            ["open", "key_down", "release_all_keys", "key_down"],
        )
        self.assertEqual(raw_device.actions[-1].args, (66,))

    def test_pause_waits_for_in_flight_input_before_releasing_keys(self) -> None:
        control = PauseController(poll_interval_seconds=0.01)
        raw_device = BlockingDryRunDevice()
        device = ControlledInputDevice(raw_device, control)

        input_finished = threading.Event()
        input_worker = threading.Thread(target=lambda: (device.key_down(65), input_finished.set()))
        input_worker.start()

        self.assertTrue(raw_device.input_started.wait(timeout=1))
        pause_finished = threading.Event()
        pause_worker = threading.Thread(target=lambda: (control.pause(), pause_finished.set()))
        pause_worker.start()
        time.sleep(0.05)

        self.assertFalse(pause_finished.is_set())

        raw_device.allow_input_finish.set()
        input_worker.join(timeout=1)
        pause_worker.join(timeout=1)

        self.assertTrue(input_finished.is_set())
        self.assertTrue(pause_finished.is_set())
        self.assertEqual(
            [action.name for action in raw_device.actions],
            ["key_down", "release_all_keys"],
        )


class BlockingDryRunDevice(DryRunDevice):
    def __init__(self) -> None:
        super().__init__()
        self.input_started = threading.Event()
        self.allow_input_finish = threading.Event()

    def key_down(self, key_code: int) -> None:
        self.input_started.set()
        self.allow_input_finish.wait(timeout=1)
        super().key_down(key_code)


if __name__ == "__main__":
    unittest.main()
