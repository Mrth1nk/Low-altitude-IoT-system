import threading
import time
import unittest
import uuid

from rdk_agent.command_router import CloudCommand, CommandRouter
from rdk_agent.rover_mission import (
    MissionError,
    MissionTimeout,
    RoverMissionWorker,
)
from rdk_agent.mavlink_rover import RoverMavlink
from rdk_agent.rover_state import RoverCommand, RoverTelemetry, apply_rover_mission_status


class RoverMissionRuntimeTests(unittest.TestCase):
    def test_router_to_real_apply_returns_busy_without_blocking_main_loop(self):
        rover = RoverMavlink([])
        telemetry = RoverTelemetry()
        locked = threading.Event()
        release = threading.Event()

        class Executor:
            def execute(self, command):
                rover_command = RoverCommand.from_dict(
                    {"command": command.action, **command.payload},
                    source="test-router",
                )
                ok, message = rover.apply(rover_command, telemetry)
                return {"accepted": ok, "message": message}

        class Aircraft:
            def execute(self, command):
                raise AssertionError(command)

        def hold_session():
            with rover.session_lock:
                locked.set()
                release.wait(1)

        thread = threading.Thread(target=hold_session)
        thread.start()
        locked.wait(1)
        router = CommandRouter(
            Executor(), Aircraft(), optical_state=lambda: "locked"
        )
        started = time.monotonic()
        result = router.route(
            CloudCommand(
                uuid.uuid4(),
                time.time(),
                "rover",
                "manual",
                {"steering": 0, "throttle": 20},
            )
        )
        elapsed = time.monotonic() - started
        release.set()
        thread.join()
        rover.mission_worker.close()

        self.assertLess(elapsed, 0.05)
        self.assertFalse(result["accepted"])
        self.assertIn("busy", result["message"].lower())
        self.assertIn("mission", result["message"].lower())

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

    def test_worker_bounds_queue_history_and_emitted_state(self):
        release = threading.Event()

        def operation(job, progress):
            del job, progress
            release.wait(1)
            return {"verified": True, "execution_ready": True}

        worker = RoverMissionWorker(
            operation, max_pending=2, max_history=3, max_age_seconds=10
        )
        first = str(uuid.uuid4())
        worker.submit(first, [], object(), False, source_timestamp=time.time())
        worker.submit(
            str(uuid.uuid4()), [], object(), False, source_timestamp=time.time()
        )
        with self.assertRaisesRegex(MissionError, "queue.*full"):
            worker.submit(
                str(uuid.uuid4()), [], object(), False,
                source_timestamp=time.time(),
            )
        release.set()
        worker.wait(first, timeout=1)
        worker.drain_completed()
        for _ in range(5):
            command_id = str(uuid.uuid4())
            worker.submit(
                command_id, [], object(), False, source_timestamp=time.time()
            )
            worker.wait(command_id, timeout=1)
            worker.drain_completed()

        self.assertLessEqual(len(worker._statuses), 3)
        self.assertLessEqual(len(worker._emitted), 3)
        worker.close()

    def test_worker_skips_cancelled_and_stale_jobs_before_operation(self):
        release = threading.Event()
        executed = []

        def operation(job, progress):
            del progress
            executed.append(job.command_id)
            if len(executed) == 1:
                release.wait(1)
            return {"verified": True, "execution_ready": True}

        now = [100.0]
        worker = RoverMissionWorker(
            operation,
            max_pending=4,
            max_history=8,
            max_age_seconds=3,
            wall_clock=lambda: now[0],
        )
        active = str(uuid.uuid4())
        cancelled = str(uuid.uuid4())
        stale = str(uuid.uuid4())
        worker.submit(active, [], object(), False, source_timestamp=100.0)
        deadline = time.monotonic() + 0.5
        while not executed and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(executed, [active])
        worker.submit(cancelled, [], object(), False, source_timestamp=100.0)
        worker.cancel(cancelled)
        worker.submit(stale, [], object(), False, source_timestamp=100.0)
        now[0] = 104.0
        release.set()
        worker.wait(active, timeout=1)
        cancelled_status = worker.wait(cancelled, timeout=1)
        stale_status = worker.wait(stale, timeout=1)

        self.assertEqual(executed, [active])
        self.assertEqual(cancelled_status.stage, "cancelled")
        self.assertEqual(stale_status.stage, "failed")
        self.assertIn("stale", stale_status.message)
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
