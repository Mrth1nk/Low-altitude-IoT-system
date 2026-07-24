import unittest

import numpy as np

from vision.config import DetectorConfig
from vision import detector as detector_module
from vision.detector import BrightSpotDetector


class BrightSpotDetectorTests(unittest.TestCase):
    def setUp(self):
        self.config = DetectorConfig(
            threshold=220,
            min_area=8.0,
            max_area=12000.0,
            min_circularity=0.2,
            min_brightness=180.0,
            blur_size=0,
            morph_kernel=0,
        )
        self.detector = BrightSpotDetector(self.config)

    def test_detects_representative_synthetic_ir_spot_with_quality(self):
        frame = np.zeros((480, 640), dtype=np.uint8)
        yy, xx = np.ogrid[:480, :640]
        frame[(xx - 370) ** 2 + (yy - 205) ** 2 <= 10**2] = 255

        result = self.detector.detect(frame, timestamp=12.5)

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.center_x, 370.0, delta=1.0)
        self.assertAlmostEqual(result.center_y, 205.0, delta=1.0)
        self.assertGreater(result.area, 200.0)
        self.assertGreater(result.confidence, 0.5)
        self.assertEqual(result.timestamp, 12.5)

    def test_rejects_small_noise_and_non_gray_input(self):
        frame = np.zeros((480, 640), dtype=np.uint8)
        frame[20:22, 20:22] = 255
        self.assertIsNone(self.detector.detect(frame, timestamp=1.0))

        with self.assertRaisesRegex(ValueError, "grayscale"):
            self.detector.detect(np.zeros((10, 10, 3), dtype=np.uint8), timestamp=1.0)

    @unittest.skipIf(detector_module.cv2 is None, "OpenCV unavailable on this host")
    def test_reports_real_opencv_backend_when_available(self):
        self.assertEqual(self.detector.backend, "opencv")


if __name__ == "__main__":
    unittest.main()
