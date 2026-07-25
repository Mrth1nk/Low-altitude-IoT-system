import unittest

from vision.runtime import (
    OpticalStatePublisher,
    is_autopilot_heartbeat,
    reopen_camera,
    request_vision_messages,
)


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


class FakeMessage:
    def __init__(self, *, vehicle_type, autopilot):
        self.type = vehicle_type
        self.autopilot = autopilot

    def get_type(self):
        return "HEARTBEAT"


class FakeMav:
    def __init__(self):
        self.calls = []

    def command_long_send(self, *args):
        self.calls.append(args)


class FakeMaster:
    def __init__(self):
        self.target_system = 1
        self.target_component = 1
        self.mav = FakeMav()


class MavlinkSetupTests(unittest.TestCase):
    def test_gcs_heartbeat_cannot_replace_flight_controller_mode(self):
        self.assertFalse(is_autopilot_heartbeat(
            FakeMessage(vehicle_type=6, autopilot=8),
        ))
        self.assertTrue(is_autopilot_heartbeat(
            FakeMessage(vehicle_type=2, autopilot=3),
        ))

    def test_requests_position_and_distance_messages_on_vision_uart(self):
        master = FakeMaster()

        request_vision_messages(master, interval_us=100_000)

        self.assertEqual(len(master.mav.calls), 3)
        self.assertEqual([call[4] for call in master.mav.calls], [33, 132, 32])
        self.assertTrue(all(call[5] == 100_000 for call in master.mav.calls))


class FakeStore:
    def __init__(self):
        self.value = None

    def save(self, value):
        self.value = value


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_optical_state_includes_control_diagnostics(self):
        store = FakeStore()

        OpticalStatePublisher(store).publish(
            locked=True,
            confidence=0.9,
            area=100,
            mode="GUIDED",
            timestamp=1.0,
            last_error="",
            altitude_m=2.0,
            altitude_source="rangefinder",
            guided_tx_count=3,
        )

        self.assertEqual(store.value["altitude_m"], 2.0)
        self.assertEqual(store.value["altitude_source"], "rangefinder")
        self.assertEqual(store.value["guided_tx_count"], 3)


if __name__ == "__main__":
    unittest.main()
