import unittest

from vision.runtime import reopen_camera


class FakeCapture:
    def __init__(self, opened=True):
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def release(self):
        self.released = True


class CameraRecoveryTests(unittest.TestCase):
    def test_reopens_camera_device_after_old_node_disappears(self):
        previous = FakeCapture()
        replacement = FakeCapture()
        opened = []

        result = reopen_camera(
            lambda device: opened.append(device) or replacement,
            "/dev/v4l/by-id/infrared-camera",
            previous,
        )

        self.assertTrue(previous.released)
        self.assertIs(result, replacement)
        self.assertEqual(opened, ["/dev/v4l/by-id/infrared-camera"])

    def test_failed_reopen_releases_unusable_capture(self):
        replacement = FakeCapture(opened=False)

        result = reopen_camera(lambda _device: replacement, "/dev/video0")

        self.assertIsNone(result)
        self.assertTrue(replacement.released)


if __name__ == "__main__":
    unittest.main()
