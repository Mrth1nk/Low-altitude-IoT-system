import unittest

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


if __name__ == "__main__":
    unittest.main()
