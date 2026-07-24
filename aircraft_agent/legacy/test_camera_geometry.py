#!/usr/bin/env python3

import unittest

from camera_geometry import camera_to_body_offsets, control_mode_for_flight_mode


class CameraGeometryTests(unittest.TestCase):
    def test_forward_mount_maps_image_top_to_aircraft_forward(self):
        forward, right = camera_to_body_offsets(
            tx=0.0,
            ty=-0.5,
            orientation="FORWARD",
        )

        self.assertAlmostEqual(forward, 0.5)
        self.assertAlmostEqual(right, 0.0)

    def test_forward_mount_maps_image_right_to_aircraft_right(self):
        forward, right = camera_to_body_offsets(
            tx=0.4,
            ty=0.0,
            orientation="FORWARD",
        )

        self.assertAlmostEqual(forward, 0.0)
        self.assertAlmostEqual(right, 0.4)

    def test_camera_offsets_are_removed_in_body_axes(self):
        forward, right = camera_to_body_offsets(
            tx=0.2,
            ty=-0.3,
            orientation="FORWARD",
            camera_offset_x=0.1,
            camera_offset_y=-0.05,
        )

        self.assertAlmostEqual(forward, 0.2)
        self.assertAlmostEqual(right, 0.25)

    def test_precision_land_modes_use_landing_target(self):
        mode = control_mode_for_flight_mode(
            configured_mode="GUIDED_VELOCITY",
            flight_mode="LAND",
            precision_land_modes=["LAND", "QLAND"],
        )

        self.assertEqual(mode, "LANDING_TARGET")

    def test_non_landing_modes_keep_configured_control(self):
        mode = control_mode_for_flight_mode(
            configured_mode="GUIDED_VELOCITY",
            flight_mode="GUIDED",
            precision_land_modes=["LAND", "QLAND"],
        )

        self.assertEqual(mode, "GUIDED_VELOCITY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
