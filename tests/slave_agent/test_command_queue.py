import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from aircraft_agent.command_worker import AircraftCommandWorker
from aircraft_agent.inbox import DurableInbox
from aircraft_agent.state_store import AtomicJsonStore
from shared_protocol.node_messages import (
    build_command_message,
    build_mission_begin_message,
    build_mission_commit_message,
    build_mission_item_message,
    mission_digest,
)


def identity(command_id=None, sequence=1, timestamp=100.0):
    return {
        "source": "rover",
        "target": "aircraft_2",
        "command_id": str(command_id or uuid.uuid4()),
        "sequence": sequence,
        "timestamp": timestamp,
    }


class Operations:
    def __init__(self):
        self.calls = []

    def set_mode(self, mode):
        self.calls.append(("mode", mode))

    def arm(self, value):
        self.calls.append(("arm", value))

    def execute(self, record, start_auto=False):
        self.calls.append(("mission", record, start_auto))


class SlaveCommandQueueTests(unittest.TestCase):
    def make_queue(self, tmp, *, max_queue=2, now=100.0):
        from slave_agent.command_queue import NodeCommandQueue, ThreadSafeInbox

        inbox = ThreadSafeInbox(DurableInbox(
            AtomicJsonStore(Path(tmp) / "inbox.json"), max_queue=max_queue
        ))
        operations = Operations()
        worker = AircraftCommandWorker(inbox, operations, start_thread=False)
        queue = NodeCommandQueue(inbox, clock=lambda: now, max_age=3.0)
        return queue, inbox, worker, operations

    def test_expired_command_is_rejected_before_durable_accept(self):
        from slave_agent.command_queue import CommandRejected

        with tempfile.TemporaryDirectory() as tmp:
            queue, inbox, _worker, _operations = self.make_queue(tmp, now=104.0)
            message = build_command_message(
                action="guided", parameters={}, **identity(timestamp=100.0)
            )

            with self.assertRaisesRegex(CommandRejected, "expired"):
                queue.accept(message)

            self.assertIsNone(inbox.active)

    def test_command_dedup_and_bounded_durable_queue(self):
        from slave_agent.command_queue import CommandRejected

        with tempfile.TemporaryDirectory() as tmp:
            queue, inbox, _worker, _operations = self.make_queue(tmp, max_queue=1)
            first = build_command_message(action="guided", **identity(sequence=1))
            second = build_command_message(action="land", **identity(sequence=2))
            third = build_command_message(action="loiter", **identity(sequence=3))

            accepted = queue.accept(first)
            duplicate = queue.accept(first)
            queue.accept(second)
            with self.assertRaisesRegex(CommandRejected, "queue_full"):
                queue.accept(third)

            self.assertEqual(accepted.stage, "QUEUED")
            self.assertFalse(accepted.duplicate)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(inbox.queue_depth, 1)

    def test_mode_and_arm_commands_execute_through_existing_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue, _inbox, worker, operations = self.make_queue(tmp)
            queue.accept(build_command_message(action="guided", **identity(sequence=1)))
            worker.run_once()
            queue.accept(build_command_message(action="arm", **identity(sequence=2)))
            worker.run_once()

            self.assertEqual(operations.calls, [("mode", "GUIDED"), ("arm", True)])

    def test_mission_is_durable_but_not_executable_until_verified_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue, inbox, worker, operations = self.make_queue(tmp)
            command_id = uuid.uuid4()
            mission_id = "slave-mission-1"
            items = [
                {"index": 0, "lat": 32.11956, "lon": 118.958406, "alt": 20.0},
                {"index": 1, "lat": 32.11976, "lon": 118.958606, "alt": 20.0},
            ]
            digest = mission_digest(items)
            common = identity(command_id, timestamp=100.0)

            queue.accept(build_mission_begin_message(
                mission_id=mission_id, item_count=2, digest=digest,
                **{**common, "sequence": 10},
            ))
            for index, item in enumerate(items):
                queue.accept(build_mission_item_message(
                    mission_id=mission_id, index=index, item=item,
                    **{**common, "sequence": 11 + index},
                ))
            self.assertIsNone(inbox.active)
            self.assertIsNone(worker.run_once())

            accepted = queue.accept(build_mission_commit_message(
                mission_id=mission_id, item_count=2, digest=digest,
                **{**common, "sequence": 13},
            ))
            result = worker.run_once()

            self.assertEqual(accepted.stage, "QUEUED")
            self.assertEqual(result["stage"], "VERIFIED")
            self.assertEqual(operations.calls[0][0], "mission")
            self.assertEqual(len(operations.calls[0][1]["items"]), 2)

    def test_bad_mission_digest_never_reaches_worker(self):
        from slave_agent.command_queue import CommandRejected

        with tempfile.TemporaryDirectory() as tmp:
            queue, inbox, worker, operations = self.make_queue(tmp)
            command_id = uuid.uuid4()
            mission_id = "bad-digest"
            item = {"index": 0, "lat": 32.1, "lon": 118.9, "alt": 10.0}
            common = identity(command_id)
            queue.accept(build_mission_begin_message(
                mission_id=mission_id, item_count=1, digest="0" * 64,
                **{**common, "sequence": 20},
            ))
            queue.accept(build_mission_item_message(
                mission_id=mission_id, index=0, item=item,
                **{**common, "sequence": 21},
            ))

            with self.assertRaisesRegex(CommandRejected, "digest_mismatch"):
                queue.accept(build_mission_commit_message(
                    mission_id=mission_id, item_count=1, digest="0" * 64,
                    **{**common, "sequence": 22},
                ))

            self.assertIsNone(inbox.active)
            self.assertIsNone(worker.run_once())
            self.assertEqual(operations.calls, [])

    def test_mission_fragments_resume_from_durable_inbox_after_restart(self):
        from slave_agent.command_queue import NodeCommandQueue, ThreadSafeInbox

        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "inbox.json"
            command_id = uuid.uuid4()
            mission_id = "restart-safe-mission"
            items = [
                {"index": 0, "lat": 32.1, "lon": 118.9, "alt": 15.0},
                {"index": 1, "lat": 32.2, "lon": 119.0, "alt": 15.0},
            ]
            digest = mission_digest(items)
            common = identity(command_id)

            first_inbox = ThreadSafeInbox(DurableInbox(AtomicJsonStore(store_path)))
            first_queue = NodeCommandQueue(first_inbox, clock=lambda: 100.0)
            first_queue.accept(build_mission_begin_message(
                mission_id=mission_id, item_count=2, digest=digest,
                **{**common, "sequence": 30},
            ))
            first_queue.accept(build_mission_item_message(
                mission_id=mission_id, index=0, item=items[0],
                **{**common, "sequence": 31},
            ))

            restarted_inbox = ThreadSafeInbox(DurableInbox(AtomicJsonStore(store_path)))
            restarted_queue = NodeCommandQueue(restarted_inbox, clock=lambda: 100.0)
            restarted_queue.accept(build_mission_item_message(
                mission_id=mission_id, index=1, item=items[1],
                **{**common, "sequence": 32},
            ))
            restarted_queue.accept(build_mission_commit_message(
                mission_id=mission_id, item_count=2, digest=digest,
                **{**common, "sequence": 33},
            ))

            self.assertEqual(restarted_inbox.active["mission_id"], mission_id)
            self.assertEqual(len(restarted_inbox.active["items"]), 2)

    def test_completion_uses_mission_commit_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue, _inbox, worker, _operations = self.make_queue(tmp)
            command_id = uuid.uuid4()
            mission_id = "completion-sequence"
            items = [{"index": 0, "lat": 32.1, "lon": 118.9, "alt": 10.0}]
            digest = mission_digest(items)
            common = identity(command_id)
            queue.accept(build_mission_begin_message(
                mission_id=mission_id, item_count=1, digest=digest,
                **{**common, "sequence": 40},
            ))
            queue.accept(build_mission_item_message(
                mission_id=mission_id, index=0, item=items[0],
                **{**common, "sequence": 41},
            ))
            queue.accept(build_mission_commit_message(
                mission_id=mission_id, item_count=1, digest=digest,
                **{**common, "sequence": 42},
            ))
            worker.run_once()

            self.assertEqual(queue.completed()[0]["sequence"], 42)

    def test_duplicate_mission_commit_is_acked_after_staging_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue, _inbox, _worker, _operations = self.make_queue(tmp)
            command_id = uuid.uuid4()
            mission_id = "duplicate-commit"
            items = [{"index": 0, "lat": 32.1, "lon": 118.9, "alt": 10.0}]
            digest = mission_digest(items)
            common = identity(command_id)
            queue.accept(build_mission_begin_message(
                mission_id=mission_id, item_count=1, digest=digest,
                **{**common, "sequence": 50},
            ))
            queue.accept(build_mission_item_message(
                mission_id=mission_id, index=0, item=items[0],
                **{**common, "sequence": 51},
            ))
            commit = build_mission_commit_message(
                mission_id=mission_id, item_count=1, digest=digest,
                **{**common, "sequence": 52},
            )

            first = queue.accept(commit)
            duplicate = queue.accept(commit)

            self.assertFalse(first.duplicate)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.sequence, 52)

    def test_thread_safe_adapter_serializes_access(self):
        from slave_agent.command_queue import ThreadSafeInbox

        class ProbeInbox:
            def __init__(self):
                self.active_calls = 0
                self.overlap = False

            def accept(self, frame):
                self.active_calls += 1
                if self.active_calls > 1:
                    self.overlap = True
                threading.Event().wait(0.01)
                self.active_calls -= 1
                return frame

        probe = ProbeInbox()
        adapter = ThreadSafeInbox(probe)
        threads = [threading.Thread(target=adapter.accept, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertFalse(probe.overlap)


if __name__ == "__main__":
    unittest.main()
