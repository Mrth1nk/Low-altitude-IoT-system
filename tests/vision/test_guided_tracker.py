import unittest

from vision.config import TrackerConfig
from vision.geometry import BodyOffset
from vision.guided_tracker import GuidedTracker


class GuidedTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = GuidedTracker(
            TrackerConfig(
                low_pass_alpha=0.5,
                deadband_m=0.03,
                gain_forward=1.0,
                gain_right=1.0,
                max_speed_mps=0.5,
                max_accel_mps2=1.0,
                stale_after_s=0.2,
            )
        )

    def test_only_guided_mode_can_emit_horizontal_correction(self):
        offset = BodyOffset(0.4, -0.2)
        self.assertIsNone(self.tracker.update(offset, mode="LOITER", frame_timestamp=1.0, now=1.0))
        self.assertIsNone(self.tracker.update(offset, mode="LAND", frame_timestamp=1.0, now=1.0))

        correction = self.tracker.update(
            offset, mode="GUIDED", frame_timestamp=1.0, now=1.0
        )
        self.assertIsNotNone(correction)
        self.assertEqual(correction.frame, "BODY_NED")
        self.assertEqual(correction.down_mps, 0.0)

    def test_deadband_speed_low_pass_and_acceleration_limits(self):
        first = self.tracker.update(
            BodyOffset(0.01, -0.01),
            mode="GUIDED",
            frame_timestamp=1.0,
            now=1.0,
        )
        self.assertEqual((first.forward_mps, first.right_mps), (0.0, 0.0))

        second = self.tracker.update(
            BodyOffset(1.0, -1.0),
            mode="GUIDED",
            frame_timestamp=1.1,
            now=1.1,
        )
        self.assertAlmostEqual(second.forward_mps, 0.1, places=6)
        self.assertAlmostEqual(second.right_mps, -0.1, places=6)

        third = self.tracker.update(
            BodyOffset(1.0, -1.0),
            mode="GUIDED",
            frame_timestamp=2.1,
            now=2.1,
        )
        self.assertAlmostEqual(third.forward_mps, 0.5, places=6)
        self.assertAlmostEqual(third.right_mps, -0.5, places=6)

    def test_stale_or_missing_target_emits_no_new_correction(self):
        self.assertIsNone(
            self.tracker.update(
                BodyOffset(0.3, 0.2),
                mode="GUIDED",
                frame_timestamp=1.0,
                now=1.21,
            )
        )
        self.assertIsNone(
            self.tracker.update(
                None,
                mode="GUIDED",
                frame_timestamp=None,
                now=1.22,
            )
        )

    def test_first_lock_starts_at_zero_before_acceleration_ramp(self):
        first = self.tracker.update(
            BodyOffset(1.0, 1.0),
            mode="GUIDED",
            frame_timestamp=4.0,
            now=4.0,
        )
        self.assertEqual((first.forward_mps, first.right_mps), (0.0, 0.0))

        second = self.tracker.update(
            BodyOffset(1.0, 1.0),
            mode="GUIDED",
            frame_timestamp=4.1,
            now=4.1,
        )
        self.assertAlmostEqual(second.forward_mps, 0.1, places=6)
        self.assertAlmostEqual(second.right_mps, 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
