#!/usr/bin/env python3

import unittest
import sys
import types
from pathlib import Path

import numpy as np

sys.modules.setdefault("cv2", types.SimpleNamespace())

import precision_land_v4
from precision_land_v4 import ArucoDetector, Config


ROOT = Path(__file__).resolve().parent


class IRDetectorTests(unittest.TestCase):
    def test_auto_threshold_stays_below_saturated_white(self):
        cfg = Config(NO_MAVLINK=True)
        cfg.IR_AUTO_THRESHOLD = True
        cfg.IR_AUTO_PERCENTILE = 99.7
        detector = ArucoDetector(cfg)
        frame = np.zeros((cfg.IMG_HEIGHT, cfg.IMG_WIDTH), dtype=np.uint8)
        frame[210:270, 290:350] = 255

        threshold = detector._threshold_for(frame)

        self.assertLess(threshold, 255)
        self.assertGreaterEqual(threshold, 0)

    def test_precision_land_service_declares_mavlink_and_ir_tuning(self):
        service = (ROOT / "light-ir-precision-land.service").read_text(encoding="utf-8")

        self.assertIn("Environment=MAV_PORT=/dev/ttyS9", service)
        self.assertIn("Environment=MAV_BAUD=57600", service)
        self.assertIn("Environment=CAM_OFFSET_X=0.04", service)
        self.assertIn("Environment=CAM_OFFSET_Y=0.01", service)
        self.assertIn("Environment=IR_AUTO_THRESHOLD=1", service)
        self.assertIn("Environment=IR_AUTO_MARGIN=8", service)
        self.assertIn("Environment=LANDING_TARGET_POSITION_VALID=1", service)
        self.assertIn("Environment=ACTIVE_MODES=GUIDED,LAND,QLAND", service)
        self.assertIn("Environment=PRECISION_LAND_MODES=LAND,QLAND", service)

    def test_landing_target_prefers_position_valid_mavlink2_payload(self):
        class FakeMav:
            def __init__(self):
                self.calls = []

            def landing_target_send(self, *args):
                self.calls.append(args)

        class FakeMaster:
            def __init__(self):
                self.mav = FakeMav()

        old_mavutil = precision_land_v4.mavutil
        precision_land_v4.mavutil = types.SimpleNamespace(
            mavlink=types.SimpleNamespace(
                MAV_FRAME_BODY_FRD=12,
                LANDING_TARGET_TYPE_LIGHT_BEACON=2,
            )
        )
        try:
            app = precision_land_v4.PrecisionLanding(Config(NO_MAVLINK=True))
            app.master = FakeMaster()

            app.send_landing_target(
                angle_x=0.05,
                angle_y=-0.04,
                distance=1.2,
                fwd_m=0.3,
                right_m=-0.2,
                down_m=1.1,
            )

            args = app.master.mav.calls[-1]
            self.assertEqual(args[2], 12)
            self.assertAlmostEqual(args[8], 0.3)
            self.assertAlmostEqual(args[9], -0.2)
            self.assertAlmostEqual(args[10], 1.1)
            self.assertEqual(args[-1], 1)
        finally:
            precision_land_v4.mavutil = old_mavutil


if __name__ == "__main__":
    unittest.main(verbosity=2)
