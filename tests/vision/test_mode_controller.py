import unittest

import numpy as np

from vision.config import CameraConfig, DetectorConfig, OpticalConfig, TrackerConfig
from vision.detector import BrightSpotDetector
from vision.guided_tracker import GuidedTracker
from vision.mode_controller import VisionModeController
from vision.optical_state import OpticalStateMachine


def bright_frame():
    frame = np.zeros((480, 640), dtype=np.uint8)
    yy, xx = np.ogrid[:480, :640]
    frame[(xx - 350) ** 2 + (yy - 210) ** 2 <= 10**2] = 255
    return frame


def centered_bright_frame():
    frame = np.zeros((480, 640), dtype=np.uint8)
    yy, xx = np.ogrid[:480, :640]
    frame[(xx - 320) ** 2 + (yy - 240) ** 2 <= 10**2] = 255
    return frame


class VisionModeControllerTests(unittest.TestCase):
    def setUp(self):
        detector = BrightSpotDetector(
            DetectorConfig(
                threshold=220,
                min_area=8,
                min_brightness=180,
                min_circularity=0.2,
                blur_size=0,
                morph_kernel=0,
            )
        )
        self.controller = VisionModeController(
            detector=detector,
            tracker=GuidedTracker(TrackerConfig()),
            optical=OpticalStateMachine(OpticalConfig(acquire_count=2, loss_count=1)),
            camera=CameraConfig(),
        )

    def test_acquire_count_then_guided_correction_and_immediate_loss_block(self):
        first = self.controller.process(
            bright_frame(), mode="GUIDED", altitude_m=2.0, timestamp=1.0, now=1.0
        )
        self.assertFalse(first.optical.locked)
        self.assertEqual(first.optical.acquire_count, 1)
        self.assertIsNone(first.correction)

        second = self.controller.process(
            bright_frame(), mode="GUIDED", altitude_m=2.0, timestamp=1.1, now=1.1
        )
        self.assertTrue(second.optical.locked)
        self.assertFalse(second.optical.blocked)
        self.assertIsNotNone(second.correction)

        lost = self.controller.process(
            np.zeros((480, 640), dtype=np.uint8),
            mode="GUIDED",
            altitude_m=2.0,
            timestamp=1.2,
            now=1.2,
        )
        self.assertTrue(lost.optical.blocked)
        self.assertFalse(lost.optical.locked)
        self.assertEqual(lost.optical.loss_count, 1)
        self.assertIsNone(lost.correction)
        self.assertTrue(lost.rc_takeover)

    def test_loiter_never_outputs_tracking_control_or_mode_change(self):
        self.controller.process(
            bright_frame(), mode="LOITER", altitude_m=2.0, timestamp=2.0, now=2.0
        )
        result = self.controller.process(
            bright_frame(), mode="LOITER", altitude_m=2.0, timestamp=2.1, now=2.1
        )
        self.assertTrue(result.optical.locked)
        self.assertIsNone(result.correction)
        self.assertIsNone(result.requested_mode)

    def test_guided_pixel_control_does_not_require_altitude(self):
        self.controller.process(
            bright_frame(),
            mode="GUIDED",
            altitude_m=0.0,
            altitude_source="unknown",
            timestamp=3.0,
            now=3.0,
        )
        result = self.controller.process(
            bright_frame(),
            mode="GUIDED",
            altitude_m=0.0,
            altitude_source="unknown",
            timestamp=3.1,
            now=3.1,
        )

        self.assertTrue(result.optical.locked)
        self.assertIsNotNone(result.correction)
        self.assertGreater(result.correction.forward_mps, 0.0)
        self.assertGreater(result.correction.right_mps, 0.0)

    def test_guided_compensates_forward_and_right_camera_displacement(self):
        self.controller = VisionModeController(
            detector=self.controller.detector,
            tracker=GuidedTracker(TrackerConfig()),
            optical=OpticalStateMachine(OpticalConfig(acquire_count=2, loss_count=1)),
            camera=CameraConfig(offset_forward_m=0.20, offset_right_m=0.20),
        )
        self.controller.process(
            centered_bright_frame(),
            mode="GUIDED",
            altitude_m=2.0,
            timestamp=4.0,
            now=4.0,
        )
        result = self.controller.process(
            centered_bright_frame(),
            mode="GUIDED",
            altitude_m=2.0,
            timestamp=4.1,
            now=4.1,
        )

        self.assertGreater(result.correction.forward_mps, 0.0)
        self.assertGreater(result.correction.right_mps, 0.0)


if __name__ == "__main__":
    unittest.main()
