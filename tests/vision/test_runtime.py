import unittest

from vision.runtime import (
    OpticalStatePublisher,
    build_preview_label,
    camera_candidates,
    is_autopilot_heartbeat,
    make_camera_capture,
    parse_final_descent_altitude,
    reopen_camera,
    request_vision_messages,
    send_target_status_text,
)


class FakeCapture:
    def __init__(self, opened=True, read_ok=True):
        self.opened = opened
        self.read_ok = read_ok
        self.released = False
        self.properties = []

    def isOpened(self):
        return self.opened

    def release(self):
        self.released = True

    def set(self, property_id, value):
        self.properties.append((property_id, value))
        return True

    def read(self):
        return self.read_ok, object()


class CameraRecoveryTests(unittest.TestCase):
    def test_camera_path_is_opened_with_v4l2_backend(self):
        calls = []

        class Cv2:
            CAP_V4L2 = 200

            @staticmethod
            def VideoCapture(*args):
                calls.append(args)
                return object()

        result = make_camera_capture(Cv2, "/dev/v4l/by-id/infrared-camera")

        self.assertIsNotNone(result)
        self.assertEqual(
            calls,
            [("/dev/v4l/by-id/infrared-camera", Cv2.CAP_V4L2)],
        )

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

    def test_reopen_tries_fallback_camera_when_primary_disappears(self):
        primary = FakeCapture(opened=False)
        fallback = FakeCapture(opened=True)
        opened = []

        def factory(device):
            opened.append(device)
            return primary if device == "/dev/video21" else fallback

        result = reopen_camera(
            factory,
            "/dev/video21",
            fallback_devices=["/dev/video0"],
        )

        self.assertIs(result, fallback)
        self.assertTrue(primary.released)
        self.assertEqual(opened, ["/dev/video21", "/dev/video0"])

    def test_reopen_skips_devices_that_open_but_cannot_read_a_frame(self):
        primary = FakeCapture(opened=True, read_ok=False)
        fallback = FakeCapture(opened=True, read_ok=True)
        opened = []

        def factory(device):
            opened.append(device)
            return primary if device == "/dev/video21" else fallback

        result = reopen_camera(
            factory,
            "/dev/video21",
            fallback_devices=["/dev/video0"],
            validate_frame=True,
        )

        self.assertIs(result, fallback)
        self.assertTrue(primary.released)
        self.assertEqual(opened, ["/dev/video21", "/dev/video0"])

    def test_camera_candidates_keep_primary_first_and_remove_duplicates(self):
        candidates = camera_candidates(
            "/dev/v4l/by-id/main",
            extra_devices=["/dev/video0", "/dev/v4l/by-id/main", "/dev/video0"],
        )

        self.assertEqual(candidates[:2], ["/dev/v4l/by-id/main", "/dev/video0"])

    def test_opened_camera_uses_reference_resolution_and_single_frame_buffer(self):
        replacement = FakeCapture()

        result = reopen_camera(
            lambda _device: replacement,
            "/dev/video0",
            width=640,
            height=480,
        )

        self.assertIs(result, replacement)
        self.assertEqual(
            replacement.properties,
            [(3, 640), (4, 480), (38, 1)],
        )


class FakeMessage:
    def __init__(self, *, vehicle_type, autopilot):
        self.type = vehicle_type
        self.autopilot = autopilot

    def get_type(self):
        return "HEARTBEAT"


class FakeMav:
    def __init__(self):
        self.calls = []
        self.status_texts = []

    def command_long_send(self, *args):
        self.calls.append(args)

    def statustext_send(self, severity, message):
        self.status_texts.append((severity, message))


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

    def test_target_lock_status_is_visible_to_ground_station(self):
        master = FakeMaster()

        send_target_status_text(master, locked=True)
        send_target_status_text(master, locked=False)

        self.assertEqual(
            [message for _severity, message in master.mav.status_texts],
            [b"IR TARGET FOUND", b"IR TARGET LOST"],
        )


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

    def test_final_descent_configuration_and_overlay(self):
        self.assertEqual(parse_final_descent_altitude("0.25"), 0.25)
        with self.assertRaises(ValueError):
            parse_final_descent_altitude("0")

        label = build_preview_label(
            mode="LAND",
            locked=True,
            confidence=0.91,
            area=12000,
            final_descent_active=True,
        )

        self.assertIn("FINAL DESCENT", label)
        self.assertIn("LAND", label)

    def test_final_descent_diagnostics_are_published(self):
        store = FakeStore()

        OpticalStatePublisher(store).publish(
            locked=True,
            confidence=0.9,
            area=12000,
            mode="LAND",
            timestamp=2.0,
            last_error="",
            final_descent_active=True,
            final_descent_altitude_m=0.25,
        )

        self.assertTrue(store.value["final_descent_active"])
        self.assertEqual(store.value["final_descent_altitude_m"], 0.25)


if __name__ == "__main__":
    unittest.main()
