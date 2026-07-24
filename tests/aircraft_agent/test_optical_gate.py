import unittest
import uuid

from aircraft_agent.optical_gate import OpticalBlocked, OpticalGate
from shared_protocol.frame import Frame, MessageType, encode_frame


def command_frame(action="guided"):
    return Frame(
        MessageType.COMMAND,
        0,
        1,
        uuid.uuid4(),
        {"action": action, "parameters": {}},
    )


class OpticalGateTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.gate = OpticalGate(clock=lambda: self.now, status_interval=1.0)

    def test_blocked_rejects_every_cloud_aircraft_command_type(self):
        command = command_frame()
        begin = Frame(
            MessageType.MISSION_BEGIN,
            0,
            2,
            uuid.uuid4(),
            {
                "mission_id": "m1",
                "item_count": 0,
                "vehicle": "aircraft",
                "checksum": "0" * 64,
            },
        )

        for frame in (command, begin):
            with self.subTest(message_type=frame.message_type):
                with self.assertRaises(OpticalBlocked):
                    self.gate.require_locked(frame)

    def test_blocked_output_is_only_timestamped_link_blocked(self):
        self.gate.set_blocked("optical_lost", timestamp=99.5)

        first = self.gate.due_frame({"mode": "GUIDED"})
        self.now = 100.4
        suppressed = self.gate.due_frame({"mode": "GUIDED"})
        self.now = 101.0
        second = self.gate.due_frame({"mode": "GUIDED"})

        self.assertEqual(first.message_type, MessageType.LINK_BLOCKED)
        self.assertEqual(first.payload["reason"], "optical_lost")
        self.assertEqual(first.payload["timestamp"], 99.5)
        self.assertIsNone(suppressed)
        self.assertEqual(second.message_type, MessageType.LINK_BLOCKED)
        self.assertNotIn("telemetry", second.payload)
        encode_frame(second)

    def test_locked_allows_commands_and_emits_bounded_snapshot_once_per_second(self):
        self.gate.set_locked(timestamp=100.0)
        self.assertTrue(self.gate.require_locked(command_frame()))

        first = self.gate.due_frame(
            {"mode": "GUIDED", "armed": True, "ignored": "x" * 1000}
        )
        self.now = 100.9
        suppressed = self.gate.due_frame({"mode": "LAND"})
        self.now = 101.0
        second = self.gate.due_frame({"mode": "LAND", "armed": False})

        self.assertEqual(first.message_type, MessageType.STATUS)
        self.assertEqual(first.payload["state"], "locked")
        self.assertEqual(first.payload["timestamp"], 100.0)
        self.assertLessEqual(len(first.payload["detail"]), 256)
        self.assertIsNone(suppressed)
        self.assertEqual(second.payload["timestamp"], 101.0)
        encode_frame(second)


if __name__ == "__main__":
    unittest.main()
