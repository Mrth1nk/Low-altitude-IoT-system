import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from aircraft_agent.inbox import DurableInbox, InboxRejected
from aircraft_agent.state_store import AtomicJsonStore
from shared_protocol.frame import Frame, MessageType


COMMAND_ID = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")


def mission_frames(command_id=COMMAND_ID, mission_id="m1"):
    items = [
        {
            "mission_id": mission_id,
            "index": index,
            "lat": 32.1 + index / 1000,
            "lon": 118.9 + index / 1000,
            "alt": 20.0,
            "command": 16,
            "frame": 6,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "autocontinue": True,
        }
        for index in range(2)
    ]
    checksum = hashlib.sha256(
        json.dumps(
            items, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
    return [
        Frame(
            MessageType.MISSION_BEGIN,
            0,
            10,
            command_id,
            {
                "mission_id": mission_id,
                "item_count": 2,
                "vehicle": "aircraft",
                "checksum": checksum,
            },
        ),
        *[
            Frame(MessageType.MISSION_ITEM, 0, 11 + index, command_id, item)
            for index, item in enumerate(items)
        ],
        Frame(
            MessageType.MISSION_COMMIT,
            0,
            13,
            command_id,
            {
                "mission_id": mission_id,
                "item_count": 2,
                "checksum": checksum,
            },
        ),
    ]


class AtomicJsonStoreTests(unittest.TestCase):
    def test_replace_file_and_directory_are_fsynced_before_save_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            events = []
            store = AtomicJsonStore(path, event_hook=events.append)

            store.save({"generation": 1})

            self.assertEqual(json.loads(path.read_text()), {"generation": 1})
            self.assertEqual(
                events[-3:], ["file_fsync", "replace", "directory_fsync"]
            )
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_rejects_state_beyond_disk_boundary_without_replacing_old_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = AtomicJsonStore(path, max_bytes=64)
            store.save({"ok": True})

            with self.assertRaisesRegex(ValueError, "state size"):
                store.save({"payload": "x" * 100})

            self.assertEqual(store.load(), {"ok": True})


class DurableInboxTests(unittest.TestCase):
    def make_inbox(self, tmp, **kwargs):
        return DurableInbox(
            AtomicJsonStore(Path(tmp) / "inbox.json"), **kwargs
        )

    def test_restart_restores_partial_mission_and_reports_missing_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            begin, first, second, commit = mission_frames()
            inbox = self.make_inbox(tmp)
            self.assertEqual(inbox.accept(begin).stage, "MISSION_STAGING")
            self.assertEqual(inbox.accept(second).stage, "MISSION_STAGING")

            recovered = self.make_inbox(tmp)

            self.assertEqual(
                recovered.resume(COMMAND_ID, "m1"), [0]
            )
            self.assertEqual(recovered.accept(first).stage, "MISSION_STAGING")
            self.assertEqual(recovered.accept(commit).stage, "MISSION_STAGED")
            self.assertEqual(recovered.active["command_id"], str(COMMAND_ID))

    def test_duplicate_frame_is_idempotent_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            begin = mission_frames()[0]
            inbox = self.make_inbox(tmp)
            first = inbox.accept(begin)
            recovered = self.make_inbox(tmp)
            duplicate = recovered.accept(begin)

            self.assertFalse(first.duplicate)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.acked_sequence, begin.sequence)

    def test_commit_with_missing_items_returns_bounded_resume_without_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            begin, first, second, commit = mission_frames()
            inbox = self.make_inbox(tmp, max_resume_items=1)
            inbox.accept(begin)

            with self.assertRaises(InboxRejected) as caught:
                inbox.accept(commit)

            self.assertEqual(caught.exception.reason, "missing_items")
            self.assertEqual(caught.exception.missing, [0])
            self.assertIsNone(inbox.active)

    def test_checksum_mismatch_is_rejected_and_partial_state_remains_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            begin, first, second, commit = mission_frames()
            inbox = self.make_inbox(tmp)
            for frame in (begin, first, second):
                inbox.accept(frame)
            bad = Frame(
                commit.message_type,
                commit.flags,
                commit.sequence,
                commit.command_id,
                {**commit.payload, "checksum": "0" * 64},
            )

            with self.assertRaisesRegex(InboxRejected, "checksum"):
                inbox.accept(bad)

            self.assertEqual(inbox.resume(COMMAND_ID, "m1"), [])
            self.assertIsNone(inbox.active)

    def test_one_active_and_bounded_fifo_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = self.make_inbox(tmp, max_queue=1)
            first = Frame(
                MessageType.COMMAND,
                0,
                1,
                uuid.uuid4(),
                {"action": "guided", "parameters": {}},
            )
            second = Frame(
                MessageType.COMMAND,
                0,
                2,
                uuid.uuid4(),
                {"action": "land", "parameters": {}},
            )
            third = Frame(
                MessageType.COMMAND,
                0,
                3,
                uuid.uuid4(),
                {"action": "rtl", "parameters": {}},
            )
            inbox.accept(first)
            inbox.accept(second)
            with self.assertRaisesRegex(InboxRejected, "queue_full"):
                inbox.accept(third)

            recovered = self.make_inbox(tmp, max_queue=1)
            self.assertEqual(recovered.active["command_id"], str(first.command_id))
            self.assertEqual(
                recovered.complete_active()["command_id"], str(first.command_id)
            )
            self.assertEqual(recovered.active["command_id"], str(second.command_id))

    def test_failed_durable_save_does_not_ack_or_pollute_in_memory_state(self):
        class FailingStore:
            def load(self, default):
                return default

            def save(self, value):
                raise OSError("disk full")

        inbox = DurableInbox(FailingStore())
        command = Frame(
            MessageType.COMMAND,
            0,
            8,
            uuid.uuid4(),
            {"action": "guided", "parameters": {}},
        )

        with self.assertRaisesRegex(OSError, "disk full"):
            inbox.accept(command)

        self.assertIsNone(inbox.active)
        self.assertEqual(inbox.queue_depth, 0)


if __name__ == "__main__":
    unittest.main()
