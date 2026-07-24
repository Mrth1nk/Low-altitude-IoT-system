import unittest
import uuid
import json

from rdk_agent.command_router import CloudCommand, CommandRejected, CommandRouter
from rdk_agent.rover_state import RoverTelemetry


class RecordingExecutor:
    def __init__(self):
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return {"accepted": True, "stage": "routed"}


class CommandRouterTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000.0
        self.rover = RecordingExecutor()
        self.aircraft = RecordingExecutor()
        self.optical_state = "locked"
        self.router = CommandRouter(
            self.rover,
            self.aircraft,
            optical_state=lambda: self.optical_state,
            clock=lambda: self.now,
            max_age_seconds=10.0,
        )

    def test_normalizes_tuya_command_into_typed_command(self):
        command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        command = CloudCommand.from_cloud(
            {
                "command_id": {"value": str(command_id)},
                "source_timestamp": {"value": int(self.now * 1000)},
                "command": {"value": "aircraft_guided"},
                "target_lat": {"value": "32.1197"},
                "target_lng": {"value": "118.9531"},
            }
        )

        self.assertEqual(command.command_id, command_id)
        self.assertEqual(command.source_timestamp, self.now)
        self.assertEqual(command.target, "aircraft")
        self.assertEqual(command.action, "guided")
        self.assertEqual(command.payload["target_lat"], 32.1197)
        self.assertEqual(command.payload["target_lng"], 118.9531)

    def test_generates_uuid_and_routes_rover_only_to_rover(self):
        command = CloudCommand.from_cloud(
            {
                "command": "manual",
                "source_timestamp": self.now,
                "steering": 25,
                "throttle": 40,
            }
        )
        result = self.router.route(command)

        self.assertIsInstance(command.command_id, uuid.UUID)
        self.assertEqual(result["stage"], "routed")
        self.assertEqual(self.rover.commands, [command])
        self.assertEqual(self.aircraft.commands, [])

    def test_routes_aircraft_only_to_aircraft_link(self):
        command = CloudCommand.from_cloud(
            {"command": "aircraft_land", "source_timestamp": self.now}
        )
        self.router.route(command)

        self.assertEqual(self.rover.commands, [])
        self.assertEqual(self.aircraft.commands, [command])

    def test_rejects_stale_command_before_any_executor(self):
        command = CloudCommand.from_cloud(
            {"command": "arm", "source_timestamp": self.now - 10.01}
        )
        with self.assertRaisesRegex(CommandRejected, "stale"):
            self.router.route(command)

        self.assertEqual(self.rover.commands, [])
        self.assertEqual(self.aircraft.commands, [])

    def test_rejects_every_aircraft_command_when_optical_link_is_blocked(self):
        self.optical_state = "blocked"
        commands = (
            {"command": "aircraft_land"},
            {"target": "aircraft", "action": "disarm"},
            {
                "target": "aircraft",
                "action": "mission",
                "payload": {"mission_id": "demo", "items": []},
            },
        )
        for raw in commands:
            raw["source_timestamp"] = self.now
            with self.assertRaisesRegex(CommandRejected, "optical"):
                self.router.route(CloudCommand.from_cloud(raw))

        self.assertEqual(self.aircraft.commands, [])

    def test_active_transaction_state_is_compact_and_contains_no_command_payload(self):
        telemetry = RoverTelemetry(
            transaction_stage="awaiting_ack",
            transaction_id="00112233-4455-6677-8899-aabbccddeeff",
            transaction_pending=4,
        )

        data = telemetry.data()

        self.assertEqual(data["tx_stage"], "awaiting_ack")
        self.assertEqual(data["tx_pending"], 4)
        self.assertNotIn("payload", data)
        self.assertNotIn("secret", repr(data).lower())
        compact = telemetry.tuya_compact_payload()["data"]["rover_state"]["value"]
        self.assertLessEqual(len(compact.encode("utf-8")), 480)
        self.assertEqual(json.loads(compact)["tx_stage"], "awaiting_ack")


if __name__ == "__main__":
    unittest.main()
