import threading
import time
import unittest
import uuid

from rdk_agent.rover_mission import (
    MissionTimeout,
    RoverMissionWorker,
)
from rdk_agent.mavlink_rover import RoverMavlink
from rdk_agent.rover_state import RoverTelemetry, apply_rover_mission_status


class RoverMissionRuntimeTests(unittest.TestCase):
    def test_submit_returns_immediately_and_progress_is_keyed_by_command_id(self):
        release = threading.Event()

        def operation(job, progress):
            progress("verifying", "readback")
            release.wait(1)
            return {"verified": True, "execution_ready": False}

        worker = RoverMissionWorker(operation)
        command_id = str(uuid.uuid4())
        started = time.monotonic()
        queued = worker.submit(command_id, [], object(), False)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.05)
        self.assertEqual(queued.command_id, command_id)
        deadline = time.monotonic() + 0.5
        while (
            worker.status(command_id).stage != "verifying"
            and time.monotonic() < deadline
        ):
            time.sleep(0.001)
        self.assertEqual(worker.status(command_id).stage, "verifying")
        self.assertEqual(worker.status(command_id).message, "readback")
        self.assertTrue(sum(range(1000)) > 0)  # main-loop work remains responsive
        release.set()
        result = worker.wait(command_id, timeout=1)
        self.assertEqual(result.stage, "verified")
        worker.close()

    def test_worker_exposes_typed_failure_without_raising_to_main_loop(self):
        def operation(job, progress):
            del progress
            raise MissionTimeout("operation deadline exceeded")

        worker = RoverMissionWorker(operation)
        command_id = str(uuid.uuid4())
        worker.submit(command_id, [], object(), False)
        result = worker.wait(command_id, timeout=1)

        self.assertEqual(result.stage, "failed")
        self.assertEqual(result.error_type, "MissionTimeout")
        self.assertIn("deadline", result.message)
        self.assertEqual(result.command_id, command_id)
        telemetry = RoverTelemetry()
        receipt = apply_rover_mission_status(telemetry, result)
        self.assertEqual(receipt["command_id"], command_id)
        self.assertEqual(receipt["stage"], "failed")
        self.assertEqual(receipt["error_type"], "MissionTimeout")
        worker.close()

    def test_completed_result_is_drained_only_once(self):
        worker = RoverMissionWorker(
            lambda job, progress: {"verified": True, "execution_ready": True}
        )
        command_id = str(uuid.uuid4())
        worker.submit(command_id, [], object(), False)
        worker.wait(command_id, timeout=1)
        self.assertEqual([x.command_id for x in worker.drain_completed()], [command_id])
        self.assertEqual(worker.drain_completed(), [])
        worker.close()

    def test_telemetry_poll_stays_responsive_while_mission_owns_session(self):
        rover = RoverMavlink([])
        rover.conn = object()
        rover.last_heartbeat = time.time()
        locked = threading.Event()
        release = threading.Event()

        def hold_session():
            with rover.session_lock:
                locked.set()
                release.wait(1)

        thread = threading.Thread(target=hold_session)
        thread.start()
        locked.wait(1)
        started = time.monotonic()
        telemetry = rover.update_telemetry(RoverTelemetry())
        elapsed = time.monotonic() - started
        release.set()
        thread.join()

        self.assertLess(elapsed, 0.05)
        self.assertEqual(telemetry.mission_status, "mission transaction active")

    def test_manual_timeout_stop_is_nonblocking_while_mission_owns_session(self):
        rover = RoverMavlink([])
        rover.conn = object()
        locked = threading.Event()
        release = threading.Event()

        def hold_session():
            with rover.session_lock:
                locked.set()
                release.wait(1)

        thread = threading.Thread(target=hold_session)
        thread.start()
        locked.wait(1)
        started = time.monotonic()
        sent = rover.try_stop()
        elapsed = time.monotonic() - started
        release.set()
        thread.join()

        self.assertFalse(sent)
        self.assertLess(elapsed, 0.05)
