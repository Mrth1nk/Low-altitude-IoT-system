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

    def test_invalid_mission_is_atomic_and_next_valid_command_works(self):
        invalid = {"mission_id": "invalid", "items": [{"lat": 32.1}]}

        with self.assertRaises(KeyError):
            self.link.execute(self.command("mission", invalid), now=0.0)

        self.assertIsNone(self.link.active_command_id)
        self.assertEqual(self.link.pending_count, 0)
        self.assertEqual(self.link.transaction_state()["transaction_id"], "")
        result = self.link.execute(self.command("guided"), now=0.0)
        self.assertEqual(result["stage"], "queued")
        self.assertEqual(self.link.pending_count, 1)

    def test_mission_queue_failure_rolls_back_every_frame_and_active_slot(self):
        original_queue = self.link.sender.queue
        calls = 0

        def fail_second(frame, now):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("queue unavailable")
            return original_queue(frame, now)

        self.link.sender.queue = fail_second
        with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
            self.link.execute(
                self.command(
                    "mission",
                    {"mission_id": "rollback", "items": mission_items()},
                ),
                now=0.0,
            )

        self.assertIsNone(self.link.active_command_id)
        self.assertEqual(self.link.pending_count, 0)
        self.assertEqual(self.link.transaction_state()["transaction_id"], "")
        self.link.sender.queue = original_queue
        result = self.link.execute(self.command("land"), now=0.0)
        self.assertEqual(result["stage"], "queued")

    def test_mission_nack_is_terminal_cancels_all_frames_and_ignores_late_ack(self):
        self.link.execute(
            self.command(
                "mission",
                {"mission_id": "terminal", "items": mission_items()},
            ),
            now=0.0,
        )
        frames = [decode_frame(blob) for blob in self.link.due_bytes(0.0)]
        self.assertEqual(len(frames), 4)
        rejected = frames[1]

        nack = Frame(
            MessageType.NACK,
            0,
            800,
            self.command_id,
            {"acked_sequence": rejected.sequence, "reason": "bad_mission_item"},
        )
        self.assertTrue(self.link.accept_response(nack))
        self.assertEqual(self.link.pending_count, 0)
        self.assertEqual(self.link.due_bytes(10.0), [])
        state = self.link.transaction_state()
        self.assertEqual(state["stage"], "nacked")
        self.assertEqual(state["error"], "bad_mission_item")

        late_ack = Frame(
            MessageType.ACK,
            0,
            801,
            self.command_id,
            {"acked_sequence": frames[-1].sequence},
        )
        self.assertFalse(self.link.accept_response(late_ack))
        self.assertEqual(self.link.transaction_state()["stage"], "nacked")

    def test_rejects_second_command_while_first_transaction_is_active(self):
        self.link.execute(self.command("guided"), now=0.0)
        second = CloudCommand(
            uuid.uuid4(), 1_800_000_001.0, "aircraft", "land", {}
        )

        with self.assertRaisesRegex(ValueError, "active"):
            self.link.execute(second, now=0.0)

        self.assertEqual(self.link.pending_count, 1)
        self.assertEqual(
            self.link.transaction_state()["transaction_id"], str(self.command_id)
        )

    def test_completed_transaction_allows_next_and_history_is_bounded(self):
        link = AircraftLink(
            max_attempts=2, retry_interval=1.0, history_limit=2
        )
        command_ids = []
        for action in ("guided", "land", "rtl"):
            command_id = uuid.uuid4()
            command_ids.append(command_id)
            link.execute(
                CloudCommand(
                    command_id, 1_800_000_000.0, "aircraft", action, {}
                ),
                now=0.0,
            )
            frame = decode_frame(link.due_bytes(0.0)[0])
            self.assertTrue(
                link.accept_response(
                    Frame(
                        MessageType.ACK,
                        0,
                        900,
                        command_id,
                        {"acked_sequence": frame.sequence},
                    )
                )
            )

        history = link.event_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(
            [item["command_id"] for item in history],
            [str(command_ids[1]), str(command_ids[2])],
        )
        self.assertTrue(all(item["stage"] == "acknowledged" for item in history))


if __name__ == "__main__":
    unittest.main()
