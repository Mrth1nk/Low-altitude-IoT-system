import unittest

from vision.config import CameraConfig
from vision.geometry import detection_to_body_offset


class GeometryTests(unittest.TestCase):
    def test_forward_camera_axes_and_mount_offsets(self):
        camera = CameraConfig(
            width=640,
            height=480,
            fx=460.0,
            fy=460.0,
            cx=320.0,
            cy=240.0,
            orientation="FORWARD",
            offset_forward_m=0.04,
            offset_right_m=0.01,
        )

        centered = detection_to_body_offset(320.0, 240.0, altitude_m=2.0, camera=camera)
        self.assertAlmostEqual(centered.forward_m, -0.04)
        self.assertAlmostEqual(centered.right_m, -0.01)

        offset = detection_to_body_offset(366.0, 194.0, altitude_m=2.0, camera=camera)
        self.assertAlmostEqual(offset.forward_m, 0.16, places=6)
        self.assertAlmostEqual(offset.right_m, 0.19, places=6)

    def test_task_seven_rejects_non_forward_mount(self):
        with self.assertRaisesRegex(ValueError, "FORWARD"):
            CameraConfig(orientation="LEFT")


if __name__ == "__main__":
    unittest.main()
