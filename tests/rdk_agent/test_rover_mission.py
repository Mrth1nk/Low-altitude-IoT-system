import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rdk_agent.rover_mission import (
    MissionDenied,
    MissionError,
    MissionItem,
    MissionTimeout,
    MissionVerificationError,
    RoverMissionManager,
    RoverMissionWorker,
)
from rdk_agent.mavlink_rover import RoverMavlink, RoverMavlinkTransport
from rdk_agent.rover_state import RoverTelemetry


FIXTURE = Path(__file__).parents[1] / "fixtures" / "rover_mission_handshake.jsonl"


class FakeTransport:
    def __init__(self, messages=(), refreshed_home=None):
        self.messages = list(messages)
        self.sent = []
        self.refreshed_home = refreshed_home
        self.home_refreshes = 0
        self.home_seen = False
        self.clock_value = 0.0

    def send(self, message_type, **fields):
        self.sent.append((message_type, fields))

    def recv(self, timeout):
        del timeout
        self.clock_value += 0.01
        return self.messages.pop(0) if self.messages else None

    def refresh_home(self, current, timeout):
        del timeout
        self.home_refreshes += 1
        if self.refreshed_home is not None:
            return self.refreshed_home
        return bool(current or self.home_seen)

    def dispatch(self, message):
        if isinstance(message, dict):
            kind = message.get("type")
            latitude = message.get("latitude")
            longitude = message.get("longitude")
        else:
            getter = getattr(message, "get_type", None)
            kind = getter() if getter else ""
            latitude = getattr(message, "latitude", None)
            longitude = getattr(message, "longitude", None)
        if kind == "HOME_POSITION":
            self.home_seen = bool(latitude and longitude)


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
    def test_execution_gate_rejects_stale_or_previous_connection_navigation(self):
        telemetry = ready_telemetry()
        telemetry.connection_generation = 2
        telemetry.gps_generation = 2
        telemetry.position_generation = 2
        telemetry.ekf_generation = 1
        telemetry.home_generation = 2
        telemetry.gps_updated_monotonic = 99.0
        telemetry.position_updated_monotonic = 99.0
        telemetry.ekf_updated_monotonic = 99.0
        telemetry.home_updated_monotonic = 99.0
        manager = RoverMissionManager(
            FakeTransport(fixture_messages()),
            timeout=0.01,
            clock=lambda: 100.0,
            navigation_freshness=3.0,
        )

        ready, reason = manager._execution_gate(
            telemetry, True, now=100.0, freshness=3.0
        )
        self.assertFalse(ready)
        self.assertIn("EKF generation", reason)

        telemetry.ekf_generation = 2
        telemetry.gps_updated_monotonic = 96.9
        ready, reason = manager._execution_gate(
            telemetry, True, now=100.0, freshness=3.0
        )
        self.assertFalse(ready)
        self.assertIn("GPS stale", reason)

    def test_disconnect_invalidates_home_and_navigation_generation(self):
        rover = RoverMavlink([])
        rover.connection_generation = 3
        rover.home_valid = True
        rover.home_updated_monotonic = 10.0
        rover.close()
        self.assertFalse(rover.home_valid)
        self.assertEqual(rover.home_updated_monotonic, 0.0)
        self.assertEqual(rover.connection_generation, 4)

    def test_repeated_valid_request_hits_per_sequence_bound_and_cleans_partial(self):
        messages = [
            {"type": "MISSION_ACK", "result": 0},
            *[{"type": "MISSION_REQUEST_INT", "seq": 1} for _ in range(5)],
            {"type": "MISSION_ACK", "result": 0},
        ]
        transport = FakeTransport(messages)
        manager = RoverMissionManager(
            transport, timeout=0.01, retries=1, max_sequence_requests=2,
            operation_timeout=1.0, clock=lambda: transport.clock_value,
        )
        with self.assertRaisesRegex(MissionError, "request limit"):
            manager.upload_and_verify(
                mission_items(), ready_telemetry(), home_valid=True
            )
        self.assertIn(
            ("MISSION_ACK", {"result": 15, "mission_type": 0}),
            transport.sent,
        )
        self.assertGreaterEqual(
            len([x for x in transport.sent if x[0] == "MISSION_CLEAR_ALL"]), 2
        )
        self.assertFalse(manager.status.verified)
        self.assertFalse(manager.status.execution_ready)

    def test_operation_deadline_does_not_reset_on_valid_requests(self):
        transport = FakeTransport(
            [{"type": "MISSION_ACK", "result": 0}]
            + [{"type": "MISSION_REQUEST_INT", "seq": 1}] * 20
            + [{"type": "MISSION_ACK", "result": 0}]
        )
        manager = RoverMissionManager(
            transport, timeout=0.01, retries=1, max_sequence_requests=100,
            operation_timeout=0.05, clock=lambda: transport.clock_value,
        )
        with self.assertRaisesRegex(MissionTimeout, "operation deadline"):
            manager.upload_and_verify(
                mission_items(), ready_telemetry(), home_valid=True
            )
        self.assertEqual(
            transport.messages,
            [],
            "failure cleanup must use its own deadline and consume clear ACK",
        )

    def test_rejects_more_than_100_items_before_clear(self):
        transport = FakeTransport()
        manager = RoverMissionManager(transport)
        with self.assertRaisesRegex(ValueError, "100"):
            manager.upload_and_verify(
                [mission_items()[0]] * 101, ready_telemetry(), home_valid=True
            )
        self.assertEqual(transport.sent, [])

    def test_readback_compares_params_and_autocontinue(self):
        for field, value in (
            ("param1", 1.0),
            ("param2", 1.0),
            ("param3", 1.0),
            ("param4", 1.0),
            ("autocontinue", 0),
        ):
            messages = fixture_messages()
            item = next(x for x in messages if x.get("seq") == 1 and x.get("type") == "MISSION_ITEM_INT")
            item[field] = value
            manager = RoverMissionManager(FakeTransport(messages), timeout=0.01)
            with self.subTest(field=field):
                with self.assertRaisesRegex(MissionVerificationError, field):
                    manager.upload_and_verify(
                        mission_items(), ready_telemetry(), home_valid=True
                    )
    def test_legacy_and_int_requests_both_receive_mission_item_int(self):
        messages = fixture_messages()
        messages[1]["type"] = "MISSION_REQUEST"
        transport = FakeTransport(messages)
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)

        manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        uploads = [
            (kind, fields["item"].seq)
            for kind, fields in transport.sent
            if kind in ("MISSION_ITEM", "MISSION_ITEM_INT")
        ]
        self.assertEqual(uploads[:3], [
            ("MISSION_ITEM_INT", 2),
            ("MISSION_ITEM_INT", 0),
            ("MISSION_ITEM_INT", 1),
        ])

    def test_float_mission_item_readback_is_scaled_to_integer_degrees(self):
        messages = fixture_messages()
        item = next(
            message for message in messages
            if message.get("type") == "MISSION_ITEM_INT" and message.get("seq") == 1
        )
        item.update(type="MISSION_ITEM", x=32.11974, y=118.95314)
        manager = RoverMissionManager(
            FakeTransport(messages), timeout=0.01, retries=1
        )

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        self.assertTrue(result.verified)

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
            if kind in ("MISSION_ITEM", "MISSION_ITEM_INT")
        ]
        self.assertEqual(sent_items[:3], [2, 0, 1])
        self.assertEqual(transport.sent[0][0], "MISSION_CLEAR_ALL")
        self.assertEqual(transport.sent[1], ("MISSION_COUNT", {"count": 3}))
        self.assertIn(("MISSION_REQUEST_LIST", {}), transport.sent)
        self.assertIn(
            ("MISSION_ACK", {"result": 0, "mission_type": 0}),
            transport.sent,
        )
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
            if kind in ("MISSION_ITEM", "MISSION_ITEM_INT")
            and fields["item"].seq == 0
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

    def test_final_reached_is_provisional_and_does_not_override_mis_done_behavior(self):
        transport = FakeTransport(fixture_messages())
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)
        manager.upload_and_verify(mission_items(), ready_telemetry(), home_valid=True)

        manager.start_auto()
        manager.observe({"type": "MISSION_CURRENT", "seq": 2})
        manager.observe({"type": "MISSION_ITEM_REACHED", "seq": 2})

        self.assertIn(("SET_MODE", {"mode": "AUTO"}), transport.sent)
        self.assertEqual(manager.status.current_seq, 2)
        self.assertTrue(manager.status.endpoint_reached)
        self.assertFalse(manager.status.completed)
        self.assertNotIn(("SET_MODE", {"mode": "HOLD"}), transport.sent)

        manager.observe({
            "type": "MISSION_CURRENT",
            "seq": 2,
            "mission_state": 5,
        })
        self.assertTrue(manager.status.completed)

    def test_status_text_can_confirm_completion_without_changing_mode(self):
        transport = FakeTransport(fixture_messages())
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)
        manager.upload_and_verify(mission_items(), ready_telemetry(), home_valid=True)
        manager.observe({"type": "MISSION_ITEM_REACHED", "seq": 2})

        manager.observe({"type": "STATUSTEXT", "text": "Mission Complete"})

        self.assertTrue(manager.status.completed)
        self.assertFalse(any(kind == "SET_MODE" for kind, _ in transport.sent))

    def test_publishes_provisional_then_confirmed_completion_for_ui(self):
        manager = RoverMissionManager(
            FakeTransport(fixture_messages()), timeout=0.01, retries=1
        )
        manager.upload_and_verify(mission_items(), ready_telemetry(), home_valid=True)
        telemetry = ready_telemetry()

        manager.observe({"type": "MISSION_ITEM_REACHED", "seq": 2})
        manager.publish_status(telemetry)
        self.assertEqual(telemetry.mission_status, "endpoint reached seq=2")

        manager.observe({
            "type": "MISSION_CURRENT",
            "seq": 2,
            "mission_state": 5,
        })
        manager.publish_status(telemetry)
        self.assertEqual(telemetry.mission_status, "mission complete seq=2")

    def test_refreshes_home_before_execution_gate(self):
        transport = FakeTransport(fixture_messages(), refreshed_home=True)
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=False
        )

        self.assertEqual(transport.home_refreshes, 1)
        self.assertTrue(result.execution_ready)

    def test_home_arriving_during_handshake_is_dispatched_and_makes_gate_ready(self):
        messages = fixture_messages()
        messages.insert(1, {
            "type": "HOME_POSITION",
            "latitude": 321197400,
            "longitude": 1189531400,
        })
        transport = FakeTransport(messages)
        manager = RoverMissionManager(transport, timeout=0.01, retries=1)

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=False
        )

        self.assertTrue(transport.home_seen)
        self.assertTrue(result.verified)
        self.assertTrue(result.execution_ready)

    def test_ignores_other_mission_types_and_wrong_targets_without_retry(self):
        messages = fixture_messages()
        messages.insert(0, {
            "type": "MISSION_ACK",
            "result": 14,
            "mission_type": 1,
            "target_system": 255,
            "target_component": 0,
            "source_system": 1,
            "source_component": 1,
        })
        messages.insert(2, {
            "type": "MISSION_REQUEST_INT",
            "seq": 99,
            "mission_type": 0,
            "target_system": 42,
            "target_component": 0,
            "source_system": 1,
            "source_component": 1,
        })
        transport = FakeTransport(messages)
        manager = RoverMissionManager(
            transport,
            timeout=0.01,
            retries=0,
            gcs_system=255,
            gcs_component=0,
            vehicle_system=1,
            vehicle_component=1,
        )

        result = manager.upload_and_verify(
            mission_items(), ready_telemetry(), home_valid=True
        )

        self.assertTrue(result.verified)
        self.assertEqual(
            len([entry for entry in transport.sent if entry[0] == "MISSION_COUNT"]),
            1,
        )

    def test_transport_requests_home_rate_limited_and_consumes_response(self):
        class FakeMav:
            def __init__(self):
                self.commands = []

            def command_long_send(self, *args):
                self.commands.append(args)

        class Home:
            latitude = 321197400
            longitude = 1189531400

            @staticmethod
            def get_type():
                return "HOME_POSITION"

        class FakeConnection:
            def __init__(self):
                self.mav = FakeMav()
                self.messages = [Home()]

            def recv_match(self, blocking, timeout):
                del blocking, timeout
                return self.messages.pop(0) if self.messages else None

        rover = RoverMavlink([])
        rover.conn = FakeConnection()
        rover.target_system = 1
        rover.target_component = 1
        rover.last_home_request = 0.0
        transport = RoverMavlinkTransport(rover, clock=lambda: 10.0)

        first = transport.refresh_home(False, timeout=0.01)
        second = transport.refresh_home(first, timeout=0.01)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(len(rover.conn.mav.commands), 1)

    def test_transport_only_encodes_mission_item_int(self):
        class FakeMav:
            def __init__(self):
                self.int_items = []

            def mission_item_int_send(self, *args):
                self.int_items.append(args)

        rover = RoverMavlink([])
        rover.conn = SimpleNamespace(mav=FakeMav())
        rover.target_system = 1
        rover.target_component = 1
        transport = RoverMavlinkTransport(rover)
        item = mission_items()[0]
        fake_mavutil = SimpleNamespace(
            mavlink=SimpleNamespace(MAV_MISSION_TYPE_MISSION=0)
        )

        with patch("rdk_agent.mavlink_rover.mavutil", fake_mavutil):
            transport.send("MISSION_ITEM_INT", item=item)

        int_args = rover.conn.mav.int_items[0]
        self.assertEqual(int_args[11], 321197400)
        self.assertEqual(int_args[12], 1189531400)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            transport.send("MISSION_ITEM", item=item)

    def test_transport_sends_final_download_ack_with_mission_type(self):
        class FakeMav:
            def __init__(self):
                self.acks = []

            def mission_ack_send(self, *args):
                self.acks.append(args)

        rover = RoverMavlink([])
        rover.conn = SimpleNamespace(mav=FakeMav())
        rover.target_system = 1
        rover.target_component = 1
        transport = RoverMavlinkTransport(rover)
        fake_mavutil = SimpleNamespace(
            mavlink=SimpleNamespace(MAV_MISSION_TYPE_MISSION=0)
        )

        with patch("rdk_agent.mavlink_rover.mavutil", fake_mavutil):
            transport.send("MISSION_ACK", result=0, mission_type=0)

        self.assertEqual(rover.conn.mav.acks, [(1, 1, 0, 0)])

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
