from __future__ import annotations

from mhscript_yjs.vision.types import Region


class MssScreenCapture:
    def __init__(self) -> None:
        import mss

        self._mss = mss.mss()

    def close(self) -> None:
        self._mss.close()

    def capture_region(self, region: Region):
        import cv2
        import numpy as np

        shot = self._mss.grab(region.as_mss())
        bgra = np.asarray(shot)
        return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
