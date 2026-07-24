import math
import unittest

import numpy as np

from vision.config import CameraConfig, DetectorConfig, OpticalConfig, TrackerConfig
from vision.detector import BrightSpotDetector, Detection
from vision.geometry import BodyOffset, detection_to_body_offset
from vision.guided_tracker import GuidedTracker
from vision.mode_controller import VisionModeController
from vision.optical_state import OpticalStateMachine
from vision.precision_landing import (
    MAV_FRAME_BODY_FRD,
    PrecisionLandingConfig,
    PrecisionLandingController,
    send_landing_target,
)


def detection(timestamp=1.0, confidence=0.9):
    return Detection(
        center_x=320.0,
        center_y=240.0,
        area=120.0,
        brightness=250.0,
        circularity=0.9,
        confidence=confidence,
        threshold=220,
        timestamp=timestamp,
    )


def bright_frame():
    frame = np.zeros((480, 640), dtype=np.uint8)
    yy, xx = np.ogrid[:480, :640]
    frame[(xx - 350) ** 2 + (yy - 210) ** 2 <= 10**2] = 255
    return frame


class FakeMav:
    def __init__(self):
        self.calls = []

    def landing_target_send(self, *args):
        self.calls.append(args)


class PrecisionLandingTests(unittest.TestCase):
    def make_controller(self, **overrides):
        values = {
            "acquire_count": 2,
            "loss_count": 2,
            "send_hz": 10.0,
            "stale_after_s": 0.2,
            "plnd_est_type": 0,
        }
        values.update(overrides)
        return PrecisionLandingController(PrecisionLandingConfig(**values))

    def acquire(
        self,
        controller,
        *,
        offset=BodyOffset(0.2, 0.1),
        altitude_m=2.0,
        start=1.0,
    ):
        controller.update(
            offset,
            detection=detection(start),
            mode="LAND",
            altitude_m=altitude_m,
            altitude_source="rangefinder",
            frame_timestamp=start,
            now=start,
        )
        return controller.update(
            offset,
            detection=detection(start + 0.1),
            mode="LAND",
            altitude_m=altitude_m,
            altitude_source="rangefinder",
            frame_timestamp=start + 0.1,
            now=start + 0.1,
        )

    def test_estimator_zero_emits_angle_only_body_frd_message(self):
        target = self.acquire(self.make_controller())

        self.assertIsNotNone(target)
        self.assertEqual(target.frame, MAV_FRAME_BODY_FRD)
        self.assertEqual(target.position_valid, 0)
        self.assertEqual((target.x, target.y, target.z), (0.0, 0.0, 0.0))
        self.assertEqual(target.q, (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(target.target_type, 0)

        mav = FakeMav()
        send_landing_target(mav, target)
        self.assertEqual(len(mav.calls), 1)
        args = mav.calls[0]
        self.assertEqual(args[2], MAV_FRAME_BODY_FRD)
        self.assertEqual(args[8:11], (0.0, 0.0, 0.0))
        self.assertEqual(args[11], (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(args[13], 0)

    def test_forward_geometry_mount_offset_and_angle_signs(self):
        camera = CameraConfig()
        altitude_m = 2.0
        center_x = camera.cx + (0.11 / altitude_m) * camera.fx
        center_y = camera.cy - (0.24 / altitude_m) * camera.fy
        offset = detection_to_body_offset(
            center_x,
            center_y,
            altitude_m=altitude_m,
            camera=camera,
        )
        self.assertAlmostEqual(offset.forward_m, 0.20, places=6)
        self.assertAlmostEqual(offset.right_m, 0.10, places=6)

        target = self.acquire(
            self.make_controller(), offset=offset, altitude_m=altitude_m
        )
        self.assertGreater(target.angle_x, 0.0)
        self.assertLess(target.angle_y, 0.0)
        self.assertAlmostEqual(target.angle_x, math.atan2(0.10, 2.0))
        self.assertAlmostEqual(target.angle_y, math.atan2(-0.20, 2.0))

    def test_lost_or_stale_frame_never_replays_previous_target(self):
        controller = self.make_controller()
        self.assertIsNotNone(self.acquire(controller))

        first_loss = controller.update(
            None,
            detection=None,
            mode="LAND",
            altitude_m=2.0,
            altitude_source="rangefinder",
            frame_timestamp=None,
            now=1.2,
        )
        self.assertIsNone(first_loss)
        self.assertTrue(controller.snapshot.acquired)
        self.assertEqual(controller.snapshot.loss_count, 1)

        second_loss = controller.update(
            None,
            detection=None,
            mode="LAND",
            altitude_m=2.0,
            altitude_source="rangefinder",
            frame_timestamp=None,
            now=1.3,
        )
        self.assertIsNone(second_loss)
        self.assertFalse(controller.snapshot.acquired)

        stale = controller.update(
            BodyOffset(0.2, 0.1),
            detection=detection(1.0),
            mode="LAND",
            altitude_m=2.0,
            altitude_source="rangefinder",
            frame_timestamp=1.0,
            now=2.0,
        )
        self.assertIsNone(stale)

    def test_acquire_hysteresis_and_send_rate_are_enforced(self):
        controller = self.make_controller(send_hz=10.0)
        first = controller.update(
            BodyOffset(0.2, 0.1),
            detection=detection(1.0),
            mode="QLAND",
            altitude_m=2.0,
            altitude_source="relative_altitude",
            frame_timestamp=1.0,
            now=1.0,
        )
        self.assertIsNone(first)

        emitted = controller.update(
            BodyOffset(0.2, 0.1),
            detection=detection(1.1),
            mode="QLAND",
            altitude_m=2.0,
            altitude_source="relative_altitude",
            frame_timestamp=1.1,
            now=1.1,
        )
        self.assertIsNotNone(emitted)
        self.assertEqual(emitted.altitude_source, "relative_altitude")
        self.assertEqual(emitted.confidence, 0.9)
        self.assertAlmostEqual(emitted.horizontal_error_m, math.hypot(0.2, 0.1))

        rate_limited = controller.update(
            BodyOffset(0.2, 0.1),
            detection=detection(1.15),
            mode="QLAND",
            altitude_m=2.0,
            altitude_source="relative_altitude",
            frame_timestamp=1.15,
            now=1.15,
        )
        self.assertIsNone(rate_limited)
        self.assertEqual(
            controller.snapshot.frequency_hz, emitted.frequency_hz
        )

        next_target = controller.update(
            BodyOffset(0.2, 0.1),
            detection=detection(1.21),
            mode="QLAND",
            altitude_m=2.0,
            altitude_source="relative_altitude",
            frame_timestamp=1.21,
            now=1.21,
        )
        self.assertIsNotNone(next_target)
        self.assertAlmostEqual(next_target.frequency_hz, 1.0 / 0.11)

    def test_invalid_altitude_and_non_landing_modes_emit_nothing(self):
        controller = self.make_controller(acquire_count=1)
        for mode, altitude in (("GUIDED", 2.0), ("LOITER", 2.0), ("LAND", 0.0)):
            result = controller.update(
                BodyOffset(0.2, 0.1),
                detection=detection(1.0),
                mode=mode,
                altitude_m=altitude,
                altitude_source="unknown",
                frame_timestamp=1.0,
                now=1.0,
            )
            self.assertIsNone(result)

    def test_mode_controller_never_crosses_guided_and_land_outputs(self):
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
        controller = VisionModeController(
            detector=detector,
            tracker=GuidedTracker(TrackerConfig()),
            optical=OpticalStateMachine(
                OpticalConfig(acquire_count=1, loss_count=1)
            ),
            camera=CameraConfig(),
            precision_landing=self.make_controller(acquire_count=1),
        )

        land = controller.process(
            bright_frame(),
            mode="LAND",
            altitude_m=2.0,
            altitude_source="rangefinder",
            timestamp=1.0,
            now=1.0,
        )
        self.assertIsNotNone(land.landing_target)
        self.assertIsNone(land.correction)

        guided = controller.process(
            bright_frame(),
            mode="GUIDED",
            altitude_m=2.0,
            altitude_source="rangefinder",
            timestamp=1.1,
            now=1.1,
        )
        self.assertIsNotNone(guided.correction)
        self.assertIsNone(guided.landing_target)


if __name__ == "__main__":
    unittest.main()
