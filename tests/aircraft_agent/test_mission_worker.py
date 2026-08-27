import hashlib
import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
import uuid

from aircraft_agent.command_worker import AircraftCommandWorker
from aircraft_agent.inbox import DurableInbox
from aircraft_agent.mavlink_session import MavlinkSession
from aircraft_agent.mission_worker import (
    AircraftMissionWorker,
    MissionDenied,
    MissionTimeout,
    MissionUnsafeResidual,
    MissionVerificationError,
)
from aircraft_agent.state_store import AtomicJsonStore
from aircraft_agent.telemetry import AircraftTelemetry
from shared_protocol.frame import Frame, MessageType
from tests.aircraft_agent.test_inbox import mission_frames


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "aircraft_mission_handshake.jsonl"
)


def fixture_messages():
    return [
        json.loads(line)
        for line in FIXTURE.read_text().splitlines()
        if line.strip()
    ]


class FakeSession:
    def __init__(self, messages=(), clock_step=0.01):
        self.messages = list(messages)
        self.sent = []
        self.now = 100.0
        self.clock_step = clock_step

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def send(self, message_type, **fields):
        self.sent.append((message_type, fields))

    def receive(self, message_types, timeout):
        del timeout
        self.now += self.clock_step
        while self.messages:
            message = self.messages.pop(0)
            if message is None:
                return None
            if message["type"] in message_types:
                return message
        return None


def staged_record():
    begin, first, second, commit = mission_frames()
    return {
        "kind": "mission",
        "command_id": str(begin.command_id),
        "mission_id": begin.payload["mission_id"],
        "items": [first.payload, second.payload],
        "checksum": commit.payload["checksum"],
        "stage": "MISSION_STAGED",
    }


def staged_single_waypoint_record():
    record = staged_record()
    items = record["items"][:1]
    return {
        **record,
        "items": items,
        "checksum": hashlib.sha256(
            json.dumps(
                items,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }


def ready_telemetry(now=100.0):
    telemetry = AircraftTelemetry(clock=lambda: now)
    telemetry.update(
        {
            "type": "GPS_RAW_INT",
            "fix_type": 3,
            "satellites_visible": 10,
        }
    )
    telemetry.update(
        {"type": "GLOBAL_POSITION_INT", "lat": 321197400, "lon": 1189531400}
    )
    telemetry.update({"type": "HOME_POSITION", "latitude": 321197400, "longitude": 1189531400})
    telemetry.update({"type": "EKF_STATUS_REPORT", "flags": 17})
    return telemetry


class MavlinkSessionTests(unittest.TestCase):
    def test_background_reader_records_serial_failure_for_runtime_recovery(self):
        class Connection:
            mav = object()

            def recv_match(self, blocking=True, timeout=None):
                del blocking, timeout
                raise OSError("serial disconnected")

        session = MavlinkSession(Connection(), telemetry=AircraftTelemetry())
        session.start_reader()
        session._reader_worker.join(0.5)

        self.assertIsInstance(session.reader_error, OSError)
        self.assertIn("serial disconnected", str(session.reader_error))

    def test_single_reader_dispatches_same_message_to_transaction_and_telemetry(self):
        class Connection:
            def __init__(self):
                self.messages = queue.Queue()
                self.mav = object()

            def recv_match(self, blocking=True, timeout=None):
                del blocking
                try:
                    return self.messages.get(timeout=timeout)
                except queue.Empty:
                    return None

        connection = Connection()
        telemetry = AircraftTelemetry(clock=lambda: 10.0)
        session = MavlinkSession(connection, telemetry=telemetry)
        observed = []
        session.subscribe(observed.append)
        with session.transaction() as transaction:
            message = {"type": "HEARTBEAT", "custom_mode": 4, "base_mode": 128}
            connection.messages.put(message)
            session.dispatch_once(timeout=0.01)

            self.assertEqual(
                transaction.receive(("HEARTBEAT",), 0.01), message
            )
            self.assertTrue(telemetry.snapshot()["armed"])
            self.assertEqual(session.reader_count, 1)
            self.assertEqual(observed, [message])

    def test_second_concurrent_transaction_is_rejected(self):
        session = MavlinkSession(object(), telemetry=AircraftTelemetry())
        first = session.transaction()
        first.__enter__()
        try:
            with self.assertRaisesRegex(RuntimeError, "transaction active"):
                session.transaction().__enter__()
        finally:
            first.__exit__(None, None, None)

    def test_background_dispatcher_is_the_only_connection_reader(self):
        class Connection:
            def __init__(self):
                self.messages = queue.Queue()
                self.mav = object()

            def recv_match(self, blocking=True, timeout=None):
                del blocking
                try:
                    return self.messages.get(timeout=timeout)
                except queue.Empty:
                    return None

        connection = Connection()
        telemetry = AircraftTelemetry(clock=time.monotonic)
        session = MavlinkSession(connection, telemetry=telemetry)
        session.start_reader()
        connection.messages.put(
            {"type": "GPS_RAW_INT", "fix_type": 3, "satellites_visible": 9}
        )
        deadline = time.monotonic() + 0.5
        while telemetry.snapshot()["gps_fix"] != 3 and time.monotonic() < deadline:
            time.sleep(0.001)
        session.close()

        self.assertEqual(telemetry.snapshot()["gps_fix"], 3)
        self.assertEqual(session.reader_count, 1)


class AircraftMissionWorkerTests(unittest.TestCase):
    def make_worker(self, session, telemetry=None, **kwargs):
        operation_timeout = kwargs.pop("operation_timeout", 1.0)
        return AircraftMissionWorker(
            session,
            telemetry or ready_telemetry(now=session.now),
            clock=lambda: session.now,
            timeout=0.05,
            operation_timeout=operation_timeout,
            **kwargs,
        )

    def test_upload_handles_legacy_and_int_requests_then_strict_readback(self):
        session = FakeSession(fixture_messages())
        result = self.make_worker(session).execute(staged_record(), start_auto=True)

        self.assertTrue(result.verified)
        self.assertTrue(result.execution_ready)
        uploaded = [
            fields["item"]["seq"]
            for kind, fields in session.sent
            if kind == "MISSION_ITEM_INT"
        ]
        self.assertEqual(uploaded, [2, 0, 1])
        protocol_home = next(
            fields["item"]
            for kind, fields in session.sent
            if kind == "MISSION_ITEM_INT" and fields["item"]["seq"] == 0
        )
        self.assertEqual(
            (protocol_home["frame"], protocol_home["x"], protocol_home["y"]),
            (0, 321197400, 1189531400),
        )
        self.assertNotIn("MISSION_ITEM", [kind for kind, _ in session.sent])
        self.assertIn(("SET_MODE", {"mode": "AUTO"}), session.sent)
        self.assertEqual(
            session.sent[-2:],
            [
                ("MISSION_ACK", {"result": 0, "mission_type": 0}),
                ("SET_MODE", {"mode": "AUTO"}),
            ],
        )

    def test_indoor_upload_and_readback_verify_but_never_enter_auto(self):
        messages = fixture_messages()
        messages[6].update({"x": 0, "y": 0, "z": 0.0})
        session = FakeSession(messages)
        telemetry = AircraftTelemetry(clock=lambda: session.now)

        result = self.make_worker(session, telemetry).execute(
            staged_record(), start_auto=True
        )

        self.assertTrue(result.verified)
        self.assertFalse(result.execution_ready)
        self.assertNotIn("SET_MODE", [kind for kind, _ in session.sent])
        self.assertIn("GPS", result.reason)

    def test_ardupilot_protocol_home_does_not_replace_first_user_waypoint(self):
        user = staged_single_waypoint_record()["items"][0]
        session = FakeSession(
            [
                {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
                {"type": "MISSION_REQUEST_INT", "seq": 1, "mission_type": 0},
                {"type": "MISSION_REQUEST_INT", "seq": 0, "mission_type": 0},
                {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
                {"type": "MISSION_COUNT", "count": 2, "mission_type": 0},
                {
                    "type": "MISSION_ITEM_INT",
                    "seq": 0,
                    "frame": 0,
                    "command": 16,
                    "x": 0,
                    "y": 0,
                    "z": 0,
                    "param1": 0,
                    "param2": 0,
                    "param3": 0,
                    "param4": 0,
                    "autocontinue": 1,
                    "mission_type": 0,
                },
                {
                    "type": "MISSION_ITEM_INT",
                    "seq": 1,
                    "frame": 3,
                    "command": user["command"],
                    "x": int(round(user["lat"] * 1e7)),
                    "y": int(round(user["lon"] * 1e7)),
                    "z": user["alt"],
                    "param1": user["param1"],
                    "param2": user["param2"],
                    "param3": user["param3"],
                    "param4": user["param4"],
                    "autocontinue": 1,
                    "mission_type": 0,
                },
            ]
        )
        telemetry = AircraftTelemetry(clock=lambda: session.now)

        result = self.make_worker(session, telemetry).execute(
            staged_single_waypoint_record(), start_auto=True
        )

        self.assertTrue(result.verified)
        self.assertFalse(result.execution_ready)
        self.assertEqual(result.item_count, 1)
        self.assertIn(
            ("MISSION_COUNT", {"count": 2, "mission_type": 0}),
            session.sent,
        )
        uploaded = [
            fields["item"]
            for kind, fields in session.sent
            if kind == "MISSION_ITEM_INT"
        ]
        self.assertEqual([item["seq"] for item in uploaded], [1, 0])
        self.assertEqual((uploaded[1]["frame"], uploaded[1]["x"], uploaded[1]["y"]), (0, 0, 0))
        self.assertEqual(
            (uploaded[0]["x"], uploaded[0]["y"]),
            (int(round(user["lat"] * 1e7)), int(round(user["lon"] * 1e7))),
        )
        self.assertNotIn("SET_MODE", [kind for kind, _ in session.sent])

    def test_frame_six_input_is_canonical_frame_three_on_wire_and_readback(self):
        record = staged_single_waypoint_record()
        self.assertEqual(record["items"][0]["frame"], 6)
        user = record["items"][0]
        session = FakeSession(
            [
                {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
                {"type": "MISSION_REQUEST_INT", "seq": 0, "mission_type": 0},
                {"type": "MISSION_REQUEST_INT", "seq": 1, "mission_type": 0},
                {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
                {"type": "MISSION_COUNT", "count": 2, "mission_type": 0},
                {
                    "type": "MISSION_ITEM_INT",
                    "seq": 0,
                    "frame": 0,
                    "command": 16,
                    "x": 0,
                    "y": 0,
                    "z": 0,
                    "autocontinue": 1,
                    "mission_type": 0,
                },
                {
                    "type": "MISSION_ITEM_INT",
                    "seq": 1,
                    "frame": 3,
                    "command": 16,
                    "x": int(round(user["lat"] * 1e7)),
                    "y": int(round(user["lon"] * 1e7)),
                    "z": user["alt"],
                    "autocontinue": 1,
                    "mission_type": 0,
                },
            ]
        )

        result = self.make_worker(
            session, AircraftTelemetry(clock=lambda: session.now)
        ).execute(record)

        self.assertTrue(result.verified)
        uploaded = [
            fields["item"]
            for kind, fields in session.sent
            if kind == "MISSION_ITEM_INT" and fields["item"]["seq"] == 1
        ]
        self.assertEqual(uploaded[0]["frame"], 3)

    def test_explicit_nonzero_simple_waypoint_param_is_rejected_before_clear(self):
        record = staged_single_waypoint_record()
        record["items"][0]["param2"] = 2.0
        record["checksum"] = hashlib.sha256(
            json.dumps(
                record["items"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        session = FakeSession(fixture_messages())

        with self.assertRaisesRegex(ValueError, "unsupported.*param2"):
            self.make_worker(session).execute(record)

        self.assertEqual(session.sent, [])

    def test_repeated_request_bound_and_global_deadline(self):
        messages = [{"type": "MISSION_ACK", "result": 0, "mission_type": 0}]
        messages.extend(
            {"type": "MISSION_REQUEST_INT", "seq": 0, "mission_type": 0}
            for _ in range(20)
        )
        messages.append({"type": "MISSION_ACK", "result": 0, "mission_type": 0})
        session = FakeSession(messages, clock_step=0.02)
        worker = self.make_worker(
            session, max_sequence_requests=3, operation_timeout=0.1
        )

        with self.assertRaises((MissionTimeout, MissionDenied)):
            worker.execute(staged_record())

        self.assertLessEqual(
            len(
                [
                    fields
                    for kind, fields in session.sent
                    if kind == "MISSION_ITEM_INT"
                    and fields["item"]["seq"] == 0
                ]
            ),
            3,
        )

    def test_filters_wrong_target_and_mission_type_without_consuming_retry(self):
        messages = fixture_messages()
        messages.insert(
            1,
            {
                "type": "MISSION_REQUEST_INT",
                "seq": 0,
                "target_system": 42,
                "mission_type": 0,
            },
        )
        messages.insert(
            2,
            {
                "type": "MISSION_REQUEST_INT",
                "seq": 0,
                "target_system": 255,
                "mission_type": 1,
            },
        )
        messages.insert(
            3,
            {
                "type": "MISSION_REQUEST_INT",
                "seq": 0,
                "target_system": 255,
                "target_component": 0,
                "source_system": 1,
                "source_component": 42,
                "mission_type": 0,
            },
        )
        session = FakeSession(messages)

        result = self.make_worker(session, retries=0).execute(staged_record())

        self.assertTrue(result.verified)
        self.assertEqual(
            [
                fields["item"]["seq"]
                for kind, fields in session.sent
                if kind == "MISSION_ITEM_INT"
            ],
            [2, 0, 1],
        )

    def test_denied_upload_ack_is_typed_and_cleanup_is_accepted(self):
        messages = fixture_messages()
        messages[4]["result"] = 14
        messages.append({"type": "MISSION_ACK", "result": 0, "mission_type": 0})
        session = FakeSession(messages)

        with self.assertRaises(MissionDenied):
            self.make_worker(session).execute(staged_record())

    def test_timeout_is_typed_when_cleanup_succeeds(self):
        session = FakeSession(
            [
                None,
                None,
                {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
            ]
        )

        with self.assertRaises(MissionTimeout):
            self.make_worker(session, retries=1).execute(staged_record())

    def test_readback_mismatch_fails_and_cleanup_requires_accepted_ack(self):
        messages = fixture_messages()
        messages[-1]["param4"] = 2.0
        messages.extend(
            [
                {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
            ]
        )
        session = FakeSession(messages)

        with self.assertRaises(MissionVerificationError):
            self.make_worker(session).execute(staged_record())

        self.assertIn(
            ("MISSION_ACK", {"result": 15, "mission_type": 0}),
            session.sent,
        )
        self.assertGreaterEqual(
            len([kind for kind, _ in session.sent if kind == "MISSION_CLEAR_ALL"]),
            2,
        )

    def test_cleanup_without_accepted_ack_marks_unsafe_and_never_auto(self):
        messages = fixture_messages()
        messages[-1]["x"] += 10
        session = FakeSession(messages)

        with self.assertRaises(MissionUnsafeResidual):
            self.make_worker(session, retries=0).execute(
                staged_record(), start_auto=True
            )

        self.assertNotIn("SET_MODE", [kind for kind, _ in session.sent])

    def test_rejects_non_durable_or_checksum_corrupt_record_before_fc_clear(self):
        for mutation in (
            {"stage": "MISSION_STAGING"},
            {"checksum": "0" * 64},
        ):
            session = FakeSession(fixture_messages())
            record = {**staged_record(), **mutation}

            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(ValueError, "durable|checksum"):
                    self.make_worker(session).execute(record)
                self.assertEqual(session.sent, [])


class AircraftCommandWorkerTests(unittest.TestCase):
    def test_only_complete_active_mission_runs_and_result_is_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = DurableInbox(AtomicJsonStore(Path(tmp) / "inbox.json"))
            for frame in mission_frames():
                inbox.accept(frame)
            session = FakeSession(fixture_messages())
            worker = AircraftCommandWorker(
                inbox,
                AircraftMissionWorker(
                    session,
                    ready_telemetry(now=session.now),
                    clock=lambda: session.now,
                    timeout=0.05,
                ),
            )

            status = worker.run_once()

            self.assertEqual(status["stage"], "VERIFIED")
            recovered = DurableInbox(
                AtomicJsonStore(Path(tmp) / "inbox.json")
            )
            self.assertIsNone(recovered.active)
            self.assertEqual(recovered.results[-1]["stage"], "VERIFIED")

    def test_mode_arm_and_mission_execute_in_fifo_order(self):
        class Operations:
            def __init__(self):
                self.calls = []

            def set_mode(self, mode):
                self.calls.append(("mode", mode))

            def arm(self, value):
                self.calls.append(("arm", value))

            def execute(self, record, start_auto=True):
                self.calls.append(
                    ("mission", record["mission_id"], bool(start_auto))
                )
                return type(
                    "Result",
                    (),
                    {"verified": True, "execution_ready": False, "reason": "GPS"},
                )()

        operations = Operations()
        worker = AircraftCommandWorker(None, operations, start_thread=False)
        worker.submit({"kind": "command", "command_id": "1", "action": "guided", "parameters": {}})
        worker.submit({"kind": "command", "command_id": "2", "action": "arm", "parameters": {}})
        worker.submit(staged_record())
        worker.run_queued()

        self.assertEqual(
            operations.calls,
            [
                ("mode", "GUIDED"),
                ("arm", True),
                ("mission", "m1", False),
            ],
        )


if __name__ == "__main__":
    unittest.main()
