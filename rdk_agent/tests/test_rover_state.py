import unittest

from rover_state import RoverCommand, RoverTelemetry, clamp, compact_json_bytes


class RoverStateTests(unittest.TestCase):
    def test_clamp_limits_numbers(self):
        self.assertEqual(clamp(200, -100, 100), 100)
        self.assertEqual(clamp(-200, -100, 100), -100)
        self.assertEqual(clamp(12, -100, 100), 12)

    def test_report_payload_wraps_each_value_for_tuya(self):
        telemetry = RoverTelemetry(lat=1.2, lng=3.4, throttle=150, flight_mode="GUIDED")
        payload = telemetry.tuya_report_payload()
        self.assertIn("msgId", payload)
        self.assertEqual(payload["data"]["lat"]["value"], 1.2)
        self.assertEqual(payload["data"]["lng"]["value"], 3.4)
        self.assertEqual(payload["data"]["throttle"]["value"], 100)
        self.assertEqual(payload["data"]["flight_mode"]["value"], "GUIDED")

    def test_command_accepts_tuya_wrapped_values(self):
        command = RoverCommand.from_tuya_data({
            "command": {"value": "manual"},
            "steering": {"value": 200},
            "throttle": {"value": -150},
        })
        self.assertEqual(command.command, "manual")
        self.assertEqual(command.steering, 100)
        self.assertEqual(command.throttle, -100)

    def test_aircraft_waypoint_keeps_altitude_while_rover_speed_is_limited(self):
        rover = RoverCommand.from_dict({"command": "waypoint", "target_speed": 20})
        aircraft = RoverCommand.from_dict({"command": "aircraft_goto", "target_speed": 20})
        aircraft_test = RoverCommand.from_dict({"command": "aircraft_goto_test_home", "target_speed": 30})

        self.assertEqual(rover.target_speed, 3.0)
        self.assertEqual(aircraft.target_speed, 20.0)
        self.assertEqual(aircraft_test.target_speed, 30.0)

    def test_compact_payload_only_uses_defined_cloud_fields(self):
        telemetry = RoverTelemetry(lat=1.2, lng=3.4, last_command="manual", steering=12, throttle=34)
        payload = telemetry.tuya_compact_payload()
        self.assertEqual(
            set(payload["data"]),
            {"rover_state", "command", "target_lat", "target_lng", "target_speed", "steering", "throttle"},
        )
        self.assertEqual(payload["data"]["command"]["value"], "manual")
        self.assertEqual(payload["data"]["steering"]["value"], "12")

    def test_compact_json_keeps_aircraft_message_text_readable(self):
        text = "心跳 GUIDED armed=NO sys=1/1"
        state = RoverTelemetry(
            lat=32.11956,
            lng=118.9584,
            mission_status="aircraft command aircraft_guided sent 2 MAVLink2 packets",
            fault_text="",
        ).data()
        state["aircraft"] = {
            "link_active": True,
            "last_seen_age_sec": 0.2,
            "last_remote": "192.168.4.1:14555 -> UDP 14560",
            "packets_received": 155,
            "bytes_received": 3966,
            "messages": [
                {"time": 1784799084.81 + idx, "type": "HEARTBEAT", "text": text}
                for idx in range(6)
            ],
        }

        payload = compact_json_bytes(state)

        self.assertLessEqual(len(payload.encode("utf-8")), 480)
        self.assertIn(text, payload)


if __name__ == "__main__":
    unittest.main()
