import unittest
import uuid

from shared_protocol.frame import Frame, MessageType
from shared_protocol.transport import (
    MissionReceiveState,
    ReceiverState,
    RetryExhausted,
    RetrySender,
)


class RetrySenderTests(unittest.TestCase):
    def setUp(self):
        self.command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        self.frame = Frame(
            MessageType.COMMAND,
            0,
            10,
            self.command_id,
            {"action": "arm", "parameters": {}},
        )

    def test_tracks_bounded_retries_until_acknowledged(self):
        sender = RetrySender(max_attempts=3, retry_interval=1.0)

        self.assertTrue(sender.queue(self.frame, now=0.0))
        self.assertEqual(sender.due(now=0.0), [self.frame])
        self.assertEqual(sender.due(now=0.5), [])
        self.assertEqual(sender.due(now=1.0), [self.frame])
        self.assertTrue(sender.acknowledge(10))
        self.assertEqual(sender.due(now=10.0), [])
        self.assertEqual(sender.pending_count, 0)

    def test_raises_after_retry_bound_and_rejects_duplicate_sequence(self):
        sender = RetrySender(max_attempts=2, retry_interval=1.0)
        sender.queue(self.frame, now=0.0)

        with self.assertRaises(ValueError):
            sender.queue(self.frame, now=0.0)

        self.assertEqual(sender.due(0.0), [self.frame])
        self.assertEqual(sender.due(1.0), [self.frame])
        with self.assertRaises(RetryExhausted) as caught:
            sender.due(2.0)
        self.assertEqual(caught.exception.sequence, 10)
        self.assertEqual(sender.pending_count, 0)


class ReceiverStateTests(unittest.TestCase):
    def setUp(self):
        self.command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

    def frame(self, message_type, sequence, payload):
        return Frame(message_type, 0, sequence, self.command_id, payload)

    def test_deduplicates_by_command_and_sequence_with_bounded_history(self):
        receiver = ReceiverState(history_limit=2)

        first = self.frame(
            MessageType.COMMAND, 1, {"action": "arm", "parameters": {}}
        )
        self.assertTrue(receiver.accept(first))
        self.assertFalse(receiver.accept(first))
        self.assertTrue(
            receiver.accept(
                self.frame(
                    MessageType.COMMAND,
                    2,
                    {"action": "disarm", "parameters": {}},
                )
            )
        )
        self.assertTrue(
            receiver.accept(
                self.frame(
                    MessageType.COMMAND,
                    3,
                    {"action": "land", "parameters": {}},
                )
            )
        )
        self.assertTrue(receiver.accept(first))

    def test_tracks_out_of_order_mission_items_and_resume_gaps(self):
        receiver = ReceiverState()
        begin = self.frame(
            MessageType.MISSION_BEGIN,
            20,
            {"mission_id": "m1", "item_count": 4, "vehicle": "aircraft"},
        )
        item_2 = self.frame(
            MessageType.MISSION_ITEM,
            22,
            {
                "mission_id": "m1",
                "index": 2,
                "lat": 32.2,
                "lon": 118.9,
                "alt": 30.0,
            },
        )
        item_0 = self.frame(
            MessageType.MISSION_ITEM,
            21,
            {
                "mission_id": "m1",
                "index": 0,
                "lat": 32.1,
                "lon": 118.8,
                "alt": 20.0,
            },
        )

        self.assertTrue(receiver.accept(begin))
        self.assertTrue(receiver.accept(item_2))
        self.assertTrue(receiver.accept(item_0))
        mission = receiver.mission("m1")
        self.assertIsInstance(mission, MissionReceiveState)
        self.assertEqual(mission.received_indices, (0, 2))
        self.assertEqual(mission.missing_indices, (1, 3))
        self.assertEqual(receiver.resume("m1"), {"missing_indices": [1, 3]})

    def test_commit_requires_complete_mission_and_matching_count(self):
        receiver = ReceiverState()
        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                1,
                {"mission_id": "m2", "item_count": 1, "vehicle": "rover"},
            )
        )
        commit = self.frame(
            MessageType.MISSION_COMMIT,
            3,
            {"mission_id": "m2", "item_count": 1},
        )
        with self.assertRaises(ValueError):
            receiver.accept(commit)

        receiver.accept(
            self.frame(
                MessageType.MISSION_ITEM,
                2,
                {
                    "mission_id": "m2",
                    "index": 0,
                    "lat": 32.1,
                    "lon": 118.8,
                    "alt": 0.0,
                },
            )
        )
        self.assertTrue(receiver.accept(commit))
        self.assertTrue(receiver.mission("m2").committed)

    def test_rejects_item_outside_declared_range_or_without_begin(self):
        receiver = ReceiverState()
        orphan = self.frame(
            MessageType.MISSION_ITEM,
            1,
            {
                "mission_id": "missing",
                "index": 0,
                "lat": 0.0,
                "lon": 0.0,
                "alt": 0.0,
            },
        )
        with self.assertRaises(ValueError):
            receiver.accept(orphan)

        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                2,
                {"mission_id": "m3", "item_count": 1, "vehicle": "aircraft"},
            )
        )
        outside = self.frame(
            MessageType.MISSION_ITEM,
            3,
            {
                "mission_id": "m3",
                "index": 1,
                "lat": 0.0,
                "lon": 0.0,
                "alt": 0.0,
            },
        )
        with self.assertRaises(ValueError):
            receiver.accept(outside)


if __name__ == "__main__":
    unittest.main()
