import unittest
import uuid

from shared_protocol.frame import Frame, MessageType
from shared_protocol.transport import (
    MissionReceiveState,
    ReceiverState,
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
        self.assertTrue(sender.acknowledge(self.command_id, 10))
        self.assertEqual(sender.due(now=10.0), [])
        self.assertEqual(sender.pending_count, 0)

    def test_pending_identity_includes_command_id_and_stale_ack_is_rejected(self):
        sender = RetrySender(max_attempts=2, retry_interval=1.0)
        other_command = uuid.UUID("11112233-4455-6677-8899-aabbccddeeff")
        same_sequence = Frame(
            MessageType.COMMAND,
            0,
            10,
            other_command,
            {"action": "land", "parameters": {}},
        )
        sender.queue(self.frame, now=0.0)
        sender.queue(same_sequence, now=0.0)

        with self.assertRaises(ValueError):
            sender.queue(self.frame, now=0.0)
        self.assertFalse(sender.acknowledge(uuid.uuid4(), 10))
        self.assertEqual(sender.pending_count, 2)
        self.assertTrue(sender.acknowledge(other_command, 10))
        self.assertEqual(sender.pending_count, 1)

    def test_exhaustion_does_not_block_other_due_entries(self):
        sender = RetrySender(max_attempts=2, retry_interval=1.0)
        sender.queue(self.frame, now=0.0)
        self.assertEqual(sender.due(0.0), [self.frame])
        self.assertEqual(sender.due(1.0), [self.frame])
        later = Frame(
            MessageType.COMMAND,
            0,
            11,
            self.command_id,
            {"action": "land", "parameters": {}},
        )
        sender.queue(later, now=2.0)

        self.assertEqual(sender.due(2.0), [later])
        self.assertEqual(sender.pop_exhausted(), [(self.command_id, 10)])
        self.assertEqual(sender.pop_exhausted(), [])
        self.assertEqual(sender.pending_count, 1)

    def test_pending_and_unread_exhaustion_state_are_bounded(self):
        sender = RetrySender(max_attempts=1, retry_interval=1.0, max_pending=1)
        sender.queue(self.frame, now=0.0)
        with self.assertRaises(ValueError):
            sender.queue(
                Frame(
                    MessageType.COMMAND,
                    0,
                    11,
                    self.command_id,
                    {"action": "land", "parameters": {}},
                ),
                now=0.0,
            )
        sender.due(0.0)
        sender.due(1.0)
        replacement = Frame(
            MessageType.COMMAND,
            0,
            11,
            self.command_id,
            {"action": "land", "parameters": {}},
        )
        sender.queue(replacement, now=2.0)
        sender.due(2.0)
        sender.due(3.0)

        self.assertEqual(sender.pop_exhausted(), [(self.command_id, 11)])


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
        self.assertTrue(receiver.accept(first, now=0.0))
        self.assertFalse(receiver.accept(first, now=0.0))
        self.assertTrue(
            receiver.accept(
                self.frame(
                    MessageType.COMMAND,
                    2,
                    {"action": "disarm", "parameters": {}},
                ),
                now=0.0,
            )
        )
        self.assertTrue(
            receiver.accept(
                self.frame(
                    MessageType.COMMAND,
                    3,
                    {"action": "land", "parameters": {}},
                ),
                now=0.0,
            )
        )
        self.assertTrue(receiver.accept(first, now=0.0))

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

        self.assertTrue(receiver.accept(begin, now=1.0))
        self.assertTrue(receiver.accept(item_2, now=2.0))
        self.assertTrue(receiver.accept(item_0, now=3.0))
        mission = receiver.mission(self.command_id, "m1", now=3.0)
        self.assertIsInstance(mission, MissionReceiveState)
        self.assertEqual(mission.received_indices, (0, 2))
        self.assertEqual(mission.missing_indices, (1, 3))
        self.assertEqual(
            receiver.resume(self.command_id, "m1", now=3.0),
            {"missing_indices": [1, 3], "next_offset": None},
        )

    def test_commit_requires_complete_mission_and_matching_count(self):
        receiver = ReceiverState()
        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                1,
                {"mission_id": "m2", "item_count": 1, "vehicle": "rover"},
            ),
            now=0.0,
        )
        commit = self.frame(
            MessageType.MISSION_COMMIT,
            3,
            {"mission_id": "m2", "item_count": 1},
        )
        with self.assertRaises(ValueError):
            receiver.accept(commit, now=1.0)

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
            ),
            now=2.0,
        )
        self.assertTrue(receiver.accept(commit, now=3.0))
        self.assertTrue(receiver.mission(self.command_id, "m2", now=3.0).committed)

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
            receiver.accept(orphan, now=0.0)

        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                2,
                {"mission_id": "m3", "item_count": 1, "vehicle": "aircraft"},
            ),
            now=0.0,
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
            receiver.accept(outside, now=0.0)

    def test_mission_transaction_is_bound_to_command_id(self):
        receiver = ReceiverState()
        other_command = uuid.UUID("11112233-4455-6677-8899-aabbccddeeff")
        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                1,
                {"mission_id": "shared", "item_count": 1, "vehicle": "aircraft"},
            ),
            now=0.0,
        )
        foreign_item = Frame(
            MessageType.MISSION_ITEM,
            0,
            2,
            other_command,
            {
                "mission_id": "shared",
                "index": 0,
                "lat": 32.1,
                "lon": 118.8,
                "alt": 10.0,
            },
        )
        with self.assertRaises(ValueError):
            receiver.accept(foreign_item, now=1.0)
        with self.assertRaises(ValueError):
            receiver.resume(other_command, "shared", now=1.0)

        receiver.accept(
            Frame(
                MessageType.MISSION_BEGIN,
                0,
                3,
                other_command,
                {
                    "mission_id": "shared",
                    "item_count": 1,
                    "vehicle": "aircraft",
                },
            ),
            now=2.0,
        )
        self.assertIsNot(
            receiver.mission(self.command_id, "shared", now=2.0),
            receiver.mission(other_command, "shared", now=2.0),
        )

    def test_bounds_staged_missions_and_expires_abandoned_state(self):
        receiver = ReceiverState(max_missions=2, mission_ttl=10.0)
        commands = [uuid.UUID(int=index + 1) for index in range(3)]

        for index in range(2):
            receiver.accept(
                Frame(
                    MessageType.MISSION_BEGIN,
                    0,
                    index,
                    commands[index],
                    {
                        "mission_id": f"m{index}",
                        "item_count": 1,
                        "vehicle": "aircraft",
                    },
                ),
                now=float(index),
            )
        with self.assertRaises(ValueError):
            receiver.accept(
                Frame(
                    MessageType.MISSION_BEGIN,
                    0,
                    3,
                    commands[2],
                    {
                        "mission_id": "m2",
                        "item_count": 1,
                        "vehicle": "aircraft",
                    },
                ),
                now=5.0,
            )

        self.assertTrue(
            receiver.accept(
                Frame(
                    MessageType.MISSION_BEGIN,
                    0,
                    3,
                    commands[2],
                    {
                        "mission_id": "m2",
                        "item_count": 1,
                        "vehicle": "aircraft",
                    },
                ),
                now=12.0,
            )
        )
        self.assertIsNone(receiver.mission(commands[0], "m0", now=12.0))

    def test_resume_paginates_missing_indexes_with_hard_page_bound(self):
        receiver = ReceiverState(max_missing_page=10)
        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                1,
                {"mission_id": "large", "item_count": 100, "vehicle": "aircraft"},
            ),
            now=0.0,
        )

        first = receiver.resume(
            self.command_id, "large", offset=0, limit=1000, now=0.0
        )
        second = receiver.resume(
            self.command_id, "large", offset=first["next_offset"], now=0.0
        )

        self.assertEqual(first, {"missing_indices": list(range(10)), "next_offset": 10})
        self.assertEqual(
            second, {"missing_indices": list(range(10, 20)), "next_offset": 20}
        )

    def test_resume_cursor_does_not_skip_when_earlier_gap_is_filled(self):
        receiver = ReceiverState(max_missing_page=2)
        receiver.accept(
            self.frame(
                MessageType.MISSION_BEGIN,
                1,
                {"mission_id": "changing", "item_count": 5, "vehicle": "aircraft"},
            ),
            now=0.0,
        )
        first = receiver.resume(self.command_id, "changing", now=0.0)
        receiver.accept(
            self.frame(
                MessageType.MISSION_ITEM,
                2,
                {
                    "mission_id": "changing",
                    "index": 0,
                    "lat": 0.0,
                    "lon": 0.0,
                    "alt": 1.0,
                },
            ),
            now=1.0,
        )

        second = receiver.resume(
            self.command_id,
            "changing",
            offset=first["next_offset"],
            now=1.0,
        )

        self.assertEqual(first, {"missing_indices": [0, 1], "next_offset": 2})
        self.assertEqual(second, {"missing_indices": [2, 3], "next_offset": 4})


if __name__ == "__main__":
    unittest.main()
