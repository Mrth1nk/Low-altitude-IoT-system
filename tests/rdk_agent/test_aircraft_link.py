import hashlib
import json
import unittest
import uuid

from rdk_agent.aircraft_link import AircraftLink
from rdk_agent.command_router import CloudCommand
from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame


def mission_items():
    return [
        {
            "lat": 32.1197, "lon": 118.9531, "alt": 20.0,
            "command": 16, "frame": 6,
            "param1": 0.0, "param2": 2.0, "param3": 0.0, "param4": 0.0,
            "autocontinue": True,
        },
        {
            "lat": 32.12, "lon": 118.954, "alt": 20.0,
            "command": 16, "frame": 6,
            "param1": 0.0, "param2": 2.0, "param3": 0.0, "param4": 0.0,
            "autocontinue": True,
        },
    ]


class AircraftLinkTests(unittest.TestCase):
    def setUp(self):
        self.command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        self.link = AircraftLink(max_attempts=3, retry_interval=1.0)

    def command(self, action="guided", payload=None):
        return CloudCommand(
            self.command_id, 1_800_000_000.0, "aircraft", action,
            {} if payload is None else payload,
        )

    def test_command_frame_and_ack_identity_include_command_id_and_sequence(self):
        self.link.execute(self.command("guided"), now=0.0)
        wire = self.link.due_bytes(now=0.0)
        self.assertEqual(len(wire), 1)
        frame = decode_frame(wire[0])
        self.assertEqual(frame.command_id, self.command_id)
        self.assertEqual(frame.payload["action"], "guided")

        stale_ack = Frame(
            MessageType.ACK, 0, 500, uuid.uuid4(),
            {"acked_sequence": frame.sequence},
        )
        self.assertFalse(self.link.accept_ack(encode_frame(stale_ack)))
        self.assertEqual(self.link.pending_count, 1)

        matching_ack = Frame(
            MessageType.ACK, 0, 501, self.command_id,
            {"acked_sequence": frame.sequence},
        )
        self.assertTrue(self.link.accept_ack(encode_frame(matching_ack)))
        self.assertEqual(self.link.pending_count, 0)

    def test_stages_mission_in_begin_item_order_then_checksum_commit(self):
        items = mission_items()
        result = self.link.execute(
            self.command("mission", {"mission_id": "flight-demo", "items": items}),
            now=0.0,
        )
        frames = [decode_frame(blob) for blob in self.link.due_bytes(now=0.0)]

        self.assertEqual(
            [frame.message_type for frame in frames],
            [MessageType.MISSION_BEGIN, MessageType.MISSION_ITEM,
             MessageType.MISSION_ITEM, MessageType.MISSION_COMMIT],
        )
        self.assertEqual(
            [frame.payload.get("index") for frame in frames], [None, 0, 1, None]
        )
        canonical = json.dumps(
            [dict(item, mission_id="flight-demo", index=index)
             for index, item in enumerate(items)],
            sort_keys=True, separators=(",", ":"),
        ).encode()
        checksum = hashlib.sha256(canonical).hexdigest()
        self.assertEqual(frames[0].payload["checksum"], checksum)
        self.assertEqual(frames[-1].payload["checksum"], checksum)
        self.assertEqual(result, {"stage": "staged", "frame_count": 4})

    def test_missing_ack_retries_only_when_due_and_reports_transaction_stage(self):
        self.link.execute(self.command("land"), now=0.0)
        first = self.link.due_bytes(now=0.0)

        self.assertEqual(len(first), 1)
        self.assertEqual(self.link.due_bytes(now=0.5), [])
        self.assertEqual(self.link.due_bytes(now=1.0), first)
        state = self.link.transaction_state()
        self.assertEqual(state["stage"], "awaiting_ack")
        self.assertEqual(state["pending"], 1)
        self.assertEqual(state["transaction_id"], str(self.command_id))

    def test_rejects_mission_item_count_beyond_protocol_bound(self):
        oversized = [mission_items()[0]] * 101
        with self.assertRaisesRegex(ValueError, "mission limit"):
            self.link.execute(
                self.command("mission", {"mission_id": "too-large", "items": oversized}),
                now=0.0,
            )


if __name__ == "__main__":
    unittest.main()
