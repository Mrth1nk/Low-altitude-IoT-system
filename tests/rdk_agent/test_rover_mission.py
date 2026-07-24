import json
from pathlib import Path
import unittest

from rdk_agent.rover_mission import (
    MissionDenied,
    MissionItem,
    MissionTimeout,
    MissionVerificationError,
    RoverMissionManager,
)
from rdk_agent.mavlink_rover import RoverMavlink
from rdk_agent.rover_state import RoverTelemetry


FIXTURE = Path(__file__).parents[1] / "fixtures" / "rover_mission_handshake.jsonl"


class FakeTransport:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.sent = []

    def send(self, message_type, **fields):
        self.sent.append((message_type, fields))

    def recv(self, timeout):
        del timeout
        return self.messages.pop(0) if self.messages else None


def fixture_messages():
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def mission_items():
    return [
        MissionItem(seq=1, frame=6, command=16, x=321197400, y=1189531400, z=0.0),
        MissionItem(seq=2, frame=6, command=16, x=321198000, y=1189538000, z=0.0),
    ]


def ready_telemetry():
    return RoverTelemetry(
        lat=32.11974,
        lng=118.95314,
        gps_fix_type=3,
        satellites_visible=10,
        ekf_flags=17,
    )


class RoverMissionManagerTests(unittest.TestCase):
    def test_accepts_real_pymavlink_ack_type_field(self):
        class Ack:
            type = 0

            @staticmethod
            def get_type():
                return "MISSION_ACK"

        messages = fixture_messages()
        messages[0] = Ack()
        upload_index = next(
            index for index, item in enumerate(messages)
            if isinstance(item, dict)
            and item.get("type") == "MISSION_ACK"
            and item.get("phase") == "upload"
        )
        messages[upload_index] = Ack()
        manager = RoverMissionManager(
            FakeTransport(messages), timeout=0.01, retries=1
        )

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        self.assertTrue(result.verified)

    def test_upload_handles_out_of_order_requests_and_verifies_download(self):
        transport = FakeTransport(fixture_messages())
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        self.assertTrue(result.verified)
        self.assertTrue(result.execution_ready)
        sent_items = [
            fields["item"].seq
            for kind, fields in transport.sent
            if kind == "MISSION_ITEM_INT"
        ]
        self.assertEqual(sent_items[:3], [2, 0, 1])
        self.assertEqual(transport.sent[0][0], "MISSION_CLEAR_ALL")
        self.assertEqual(transport.sent[1], ("MISSION_COUNT", {"count": 3}))
        self.assertIn(("MISSION_REQUEST_LIST", {}), transport.sent)
        requested = [
            fields["seq"]
            for kind, fields in transport.sent
            if kind == "MISSION_REQUEST_INT"
        ]
        self.assertEqual(requested, [0, 1, 2])

    def test_item_zero_is_protocol_home_placeholder_not_an_execution_waypoint(self):
        transport = FakeTransport(fixture_messages())
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)
        manager.upload_and_verify(mission_items(), ready_telemetry(), home_valid=True)

        home = next(
            fields["item"]
            for kind, fields in transport.sent
            if kind == "MISSION_ITEM_INT" and fields["item"].seq == 0
        )
        self.assertTrue(home.is_home)
        self.assertEqual((home.x, home.y, home.z), (0, 0, 0.0))
        self.assertEqual(manager.executable_items, mission_items())

    def test_retries_count_after_timeout_and_repeated_request_is_idempotent(self):
        messages = fixture_messages()
        messages.insert(1, None)
        messages.insert(4, {"type": "MISSION_REQUEST_INT", "seq": 2})
        transport = FakeTransport(messages)
        manager = RoverMissionManager(transport, timeout=0.01, retries=2)

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        self.assertTrue(result.verified)
        counts = [entry for entry in transport.sent if entry[0] == "MISSION_COUNT"]
        self.assertEqual(len(counts), 2)
        sent_twos = [
            entry for entry in transport.sent
            if entry[0] == "MISSION_ITEM_INT" and entry[1]["item"].seq == 2
        ]
        self.assertEqual(len(sent_twos), 2)

    def test_unrelated_telemetry_does_not_consume_retry_budget(self):
        messages = fixture_messages()
        messages.insert(0, {"type": "HEARTBEAT"})
        count_index = next(
            index for index, item in enumerate(messages)
            if item.get("type") == "MISSION_COUNT"
        )
        messages.insert(count_index, {"type": "GPS_RAW_INT"})
        transport = FakeTransport(messages)
        manager = RoverMissionManager(transport, timeout=0.01, retries=0)

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        self.assertTrue(result.verified)
        self.assertEqual(
            len([entry for entry in transport.sent if entry[0] == "MISSION_CLEAR_ALL"]),
            1,
        )
        self.assertEqual(
            len([entry for entry in transport.sent if entry[0] == "MISSION_REQUEST_LIST"]),
            1,
        )

    def test_timeout_after_bounded_retries(self):
        transport = FakeTransport(
            [{"type": "MISSION_ACK", "result": 0, "phase": "clear"}]
        )
        manager = RoverMissionManager(transport, timeout=0.01, retries=2)

        with self.assertRaises(MissionTimeout):
            manager.upload_and_verify(
                mission_items(), ready_telemetry(), home_valid=True
            )
        self.assertEqual(
            len([entry for entry in transport.sent if entry[0] == "MISSION_COUNT"]),
            3,
        )

    def test_denied_upload_ack_fails(self):
        messages = fixture_messages()
        upload_ack = next(
            index for index, item in enumerate(messages)
            if item.get("type") == "MISSION_ACK" and item.get("phase") == "upload"
        )
        messages[upload_ack]["result"] = 14
        manager = RoverMissionManager(
            FakeTransport(messages), timeout=0.01, retries=1
        )

        with self.assertRaises(MissionDenied):
            manager.upload_and_verify(
                mission_items(), ready_telemetry(), home_valid=True
            )

    def test_readback_coordinate_mismatch_fails_verification(self):
        messages = fixture_messages()
        item = next(
            message for message in messages
            if message.get("type") == "MISSION_ITEM_INT" and message.get("seq") == 2
        )
        item["x"] += 10
        manager = RoverMissionManager(
            FakeTransport(messages), timeout=0.01, retries=1
        )

        with self.assertRaisesRegex(MissionVerificationError, "seq 2"):
            manager.upload_and_verify(
                mission_items(), ready_telemetry(), home_valid=True
            )

    def test_indoor_upload_verifies_but_auto_is_gated(self):
        telemetry = RoverTelemetry(
            lat=0,
            lng=0,
            gps_fix_type=1,
            satellites_visible=0,
            ekf_flags=0,
        )
        transport = FakeTransport(fixture_messages())
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)

        result = manager.upload_and_verify(
            mission_items(), telemetry, home_valid=False
        )

        self.assertTrue(result.verified)
        self.assertFalse(result.execution_ready)
        with self.assertRaisesRegex(RuntimeError, "not execution ready"):
            manager.start_auto()
        self.assertNotIn(("SET_MODE", {"mode": "AUTO"}), transport.sent)

    def test_auto_requires_verified_and_ready_then_tracks_endpoint(self):
        transport = FakeTransport(fixture_messages())
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)
        manager.upload_and_verify(mission_items(), ready_telemetry(), home_valid=True)

        manager.start_auto()
        manager.observe({"type": "MISSION_CURRENT", "seq": 2})
        manager.observe({"type": "MISSION_ITEM_REACHED", "seq": 2})

        self.assertIn(("SET_MODE", {"mode": "AUTO"}), transport.sent)
        self.assertEqual(manager.status.current_seq, 2)
        self.assertTrue(manager.status.completed)
        self.assertIn(("SET_MODE", {"mode": "HOLD"}), transport.sent)

    def test_manual_rc_override_behavior_is_preserved(self):
        class FakeMav:
            def __init__(self):
                self.override = []

            def rc_channels_override_send(self, *args):
                self.override.append(args)

        class FakeConnection:
            def __init__(self):
                self.mav = FakeMav()

        rover = RoverMavlink([])
        rover.conn = FakeConnection()
        rover.target_system = 1
        rover.target_component = 0
        rover.rc_override(25, 50)

        args = rover.conn.mav.override[-1]
        self.assertEqual(args[2], rover.scale_rc(25, 1000, 1500, 2000))
        self.assertEqual(args[4], rover.scale_rc(50, 1000, 1500, 2000))


if __name__ == "__main__":
    unittest.main()
