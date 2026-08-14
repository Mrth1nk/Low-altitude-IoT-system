import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mavlink_rover import RoverMavlink, discover_mavlink_urls
from rover_state import RoverCommand, RoverTelemetry


class FakeRover:
    def __init__(self):
        self.calls = []

    def connect(self, timeout=0.5):
        return False


class MavlinkRoverTests(unittest.TestCase):
    def test_discover_keeps_configured_url_first(self):
        urls = discover_mavlink_urls("/dev/test0,/dev/test1")
        self.assertEqual(urls[0], "/dev/test0")
        self.assertEqual(urls[1], "/dev/test1")

    def test_command_model_updates_telemetry_fields_before_apply(self):
        command = RoverCommand.from_dict({"command": "manual", "steering": 12, "throttle": 34})
        telemetry = RoverTelemetry()
        telemetry.last_command = command.command
        telemetry.steering = command.steering
        telemetry.throttle = command.throttle
        self.assertEqual(telemetry.last_command, "manual")
        self.assertEqual(telemetry.steering, 12)
        self.assertEqual(telemetry.throttle, 34)

    def test_rc_scale_uses_trim_as_zero(self):
        rover = RoverMavlink([])
        self.assertEqual(rover.scale_rc(0, 945, 1573, 1998), 1573)
        self.assertEqual(rover.scale_rc(100, 945, 1573, 1998), 1998)
        self.assertEqual(rover.scale_rc(-100, 945, 1573, 1998), 945)

    def test_rc_scale_maps_forward_and_reverse_from_trim(self):
        rover = RoverMavlink([])
        self.assertEqual(rover.scale_rc(50, 945, 1573, 1998), 1785)
        self.assertEqual(rover.scale_rc(-50, 945, 1573, 1998), 1259)

    def test_set_mode_waits_for_matching_flight_controller_heartbeat(self):
        class Heartbeat:
            mode = "AUTO"

            @staticmethod
            def get_srcSystem():
                return 1

        class Connection:
            def __init__(self):
                self.sent = []
                self.messages = [Heartbeat()]

            @staticmethod
            def mode_mapping():
                return {"HOLD": 4, "AUTO": 10}

            def set_mode(self, mode_id):
                self.sent.append(mode_id)

            def recv_match(self, **_kwargs):
                return self.messages.pop(0) if self.messages else None

        rover = RoverMavlink([])
        rover.conn = Connection()
        rover.target_system = 1
        fake_mavutil = SimpleNamespace(mode_string_v10=lambda message: message.mode)

        with patch("mavlink_rover.mavutil", fake_mavutil):
            confirmed = rover.set_mode("AUTO", timeout=0.05)

        self.assertEqual(confirmed, "AUTO")
        self.assertEqual(rover.conn.sent, [10])
        rover.mission_worker.close()

    def test_set_mode_rejects_unconfirmed_mode_instead_of_false_success(self):
        class Heartbeat:
            mode = "HOLD"

            @staticmethod
            def get_srcSystem():
                return 1

        class Connection:
            def __init__(self):
                self.messages = [Heartbeat()]

            @staticmethod
            def mode_mapping():
                return {"HOLD": 4, "AUTO": 10}

            @staticmethod
            def set_mode(_mode_id):
                return None

            def recv_match(self, **_kwargs):
                return self.messages.pop(0) if self.messages else None

        rover = RoverMavlink([])
        rover.conn = Connection()
        rover.target_system = 1
        fake_mavutil = SimpleNamespace(mode_string_v10=lambda message: message.mode)

        with patch("mavlink_rover.mavutil", fake_mavutil):
            with self.assertRaisesRegex(RuntimeError, "AUTO.*HOLD"):
                rover.set_mode("AUTO", timeout=0.05)
        rover.mission_worker.close()

    def test_auto_without_verified_mission_is_rejected_before_mode_send(self):
        rover = RoverMavlink([])
        rover.connect = lambda timeout=0.5: True
        sent_modes = []
        rover.set_mode = lambda mode: sent_modes.append(mode)

        ok, detail = rover.apply(
            RoverCommand.from_dict({"command": "auto"}),
            RoverTelemetry(),
        )

        self.assertFalse(ok)
        self.assertIn("mission re-upload required", detail.lower())
        self.assertEqual(sent_modes, [])
        rover.mission_worker.close()

    def test_auto_refreshes_navigation_and_waits_for_confirmed_mode(self):
        class MissionManager:
            def __init__(self):
                self.status = SimpleNamespace(
                    verified=True,
                    execution_ready=False,
                    residual_unsafe=False,
                )
                self.transport = SimpleNamespace(
                    refresh_home=lambda current, timeout: True,
                    sync_navigation=lambda telemetry: None,
                )
                self.refreshed = []
                self.executable_items = [object()]

            def refresh_execution_readiness(self, telemetry, home_valid, now=None):
                self.refreshed.append((telemetry, home_valid))
                self.status.execution_ready = True
                return True, ""

        rover = RoverMavlink([])
        rover.connect = lambda timeout=0.5: True
        rover.mission_manager = MissionManager()
        confirmed = []
        rover.set_mode = lambda mode, timeout=2.5: confirmed.append(mode) or mode
        telemetry = RoverTelemetry()

        ok, detail = rover.apply(
            RoverCommand.from_dict({"command": "auto"}),
            telemetry,
        )

        self.assertTrue(ok, detail)
        self.assertEqual(confirmed, ["AUTO"])
        self.assertEqual(telemetry.flight_mode, "auto")
        self.assertEqual(len(rover.mission_manager.refreshed), 1)
        rover.mission_worker.close()

    def test_auto_reports_unconfirmed_last_observed_mode(self):
        class MissionManager:
            status = SimpleNamespace(
                verified=True,
                execution_ready=True,
                residual_unsafe=False,
            )
            transport = SimpleNamespace(
                refresh_home=lambda current, timeout: True,
                sync_navigation=lambda telemetry: None,
            )
            executable_items = [object()]

            @staticmethod
            def refresh_execution_readiness(telemetry, home_valid, now=None):
                return True, ""

        rover = RoverMavlink([])
        rover.connect = lambda timeout=0.5: True
        rover.mission_manager = MissionManager()
        rover.set_mode = lambda mode, timeout=2.5: (_ for _ in ()).throw(
            RuntimeError("mode AUTO not confirmed; flight controller reports HOLD")
        )

        ok, detail = rover.apply(
            RoverCommand.from_dict({"command": "auto"}),
            RoverTelemetry(),
        )

        self.assertFalse(ok)
        self.assertIn("AUTO", detail)
        self.assertIn("HOLD", detail)
        rover.mission_worker.close()


if __name__ == "__main__":
    unittest.main()
