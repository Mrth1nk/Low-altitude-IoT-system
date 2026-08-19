import json
import unittest
from unittest.mock import patch

from rover_state import (
    RoverCommand,
    RoverTelemetry,
    clamp,
    compact_json_bytes,
    record_command_receipt,
)


class RoverStateTests(unittest.TestCase):
    def test_clamp_limits_numbers(self):
        self.assertEqual(clamp(200, -100, 100), 100)
        self.assertEqual(clamp(-200, -100, 100), -100)
        self.assertEqual(clamp(12, -100, 100), 12)

    def test_report_payload_wraps_each_value_for_tuya(self):
        telemetry = RoverTelemetry(lat=1.2, lng=3.4, throttle=150, flight_mode="GUIDED")
        payload = telemetry.tuya_report_payload()
        self.assertIn("msgId", payload)
        self.assertEqual(payload["data"]["lat"]["value"], 1.2)
        self.assertEqual(payload["data"]["lng"]["value"], 3.4)
        self.assertEqual(payload["data"]["throttle"]["value"], 100)
        self.assertEqual(payload["data"]["flight_mode"]["value"], "GUIDED")

    def test_command_accepts_tuya_wrapped_values(self):
        command = RoverCommand.from_tuya_data({
            "command": {"value": "manual"},
            "steering": {"value": 200},
            "throttle": {"value": -150},
        })
        self.assertEqual(command.command, "manual")
        self.assertEqual(command.steering, 100)
        self.assertEqual(command.throttle, -100)

    def test_aircraft_waypoint_keeps_altitude_while_rover_speed_is_limited(self):
        rover = RoverCommand.from_dict({"command": "waypoint", "target_speed": 20})
        aircraft = RoverCommand.from_dict({"command": "aircraft_goto", "target_speed": 20})
        aircraft_test = RoverCommand.from_dict({"command": "aircraft_goto_test_home", "target_speed": 30})

        self.assertEqual(rover.target_speed, 3.0)
        self.assertEqual(aircraft.target_speed, 20.0)
        self.assertEqual(aircraft_test.target_speed, 30.0)

    def test_compact_payload_only_uses_defined_cloud_fields(self):
        telemetry = RoverTelemetry(lat=1.2, lng=3.4, last_command="manual", steering=12, throttle=34)
        payload = telemetry.tuya_compact_payload()
        self.assertEqual(
            set(payload["data"]),
            {"rover_state", "target_lat", "target_lng", "target_speed", "steering", "throttle"},
        )
        self.assertEqual(payload["data"]["steering"]["value"], "12")

    def test_slave_state_is_added_beside_byte_identical_rover_state(self):
        telemetry = RoverTelemetry(lat=1.2, lng=3.4, last_command="manual")
        extra = {"aircraft_link": True, "aircraft_mode": "LOITER"}
        with patch("rover_state.time.time", return_value=1800000000.0):
            original = telemetry.tuya_compact_payload(extra)
        slave = {
            "online": True,
            "fc_connected": True,
            "blocked": False,
            "mode": "GUIDED",
            "armed": False,
            "lat": 32.11974,
            "lon": 118.95314,
            "position_observed": True,
            "altitude": 5.25,
            "speed": 1.2,
            "heading": 181.5,
            "battery": 88,
            "mission_stage": "EXECUTING",
            "mission_id": "m" * 120,
            "fault": "故障" * 200,
            "link_state": "ONLINE",
            "event": {"timestamp": 1000.0, "sequence": 9, "type": "MISSION", "text": "状态" * 200},
        }

        with patch("rover_state.time.time", return_value=1800000000.0):
            combined = telemetry.tuya_compact_payload(extra, slave_state=slave)

        self.assertEqual(
            combined["data"]["rover_state"]["value"],
            original["data"]["rover_state"]["value"],
        )
        encoded = combined["data"]["slave_state"]["value"]
        self.assertLessEqual(len(encoded.encode("utf-8")), 480)
        decoded = json.loads(encoded)
        self.assertTrue(decoded["online"])
        self.assertEqual(decoded["mode"], "GUIDED")
        self.assertEqual(decoded["mission_stage"], "EXECUTING")

    def test_no_slave_state_keeps_existing_cloud_property_set(self):
        telemetry = RoverTelemetry()
        payload = telemetry.tuya_compact_payload()

        self.assertNotIn("slave_state", payload["data"])
        self.assertEqual(
            set(payload["data"]),
            {"rover_state", "target_lat", "target_lng", "target_speed", "steering", "throttle"},
        )

    def test_position_observation_distinguishes_real_zero_from_no_frame(self):
        telemetry = RoverTelemetry(lat=0.0, lng=0.0)
        self.assertFalse(telemetry.data()["position_observed"])

        telemetry.position_observed = True
        data = telemetry.data()

        self.assertTrue(data["position_observed"])
        self.assertEqual((data["lat"], data["lng"]), (0.0, 0.0))

    def test_compact_state_keeps_both_observed_positions_and_auto_fault(self):
        telemetry = RoverTelemetry(
            lat=0.0,
            lng=0.0,
            position_observed=True,
            flight_mode="hold",
            gps_fix_type=1,
            satellites_visible=0,
            mission_status="command_failed",
            fault_text="AUTO blocked: missing GPS fix, satellites, location, Home, EKF",
            rover_transaction_stage="failed",
            rover_transaction_id="r" * 64,
            last_command="auto",
        )
        state = telemetry.data()
        state.update({
            "aircraft_link": True,
            "aircraft_mode": "LOITER",
            "aircraft_armed": False,
            "aircraft_lat": 0.0,
            "aircraft_lng": 0.0,
            "aircraft_position_observed": True,
            "aircraft": {
                "link_active": True,
                "position_observed": True,
                "lat": 0.0,
                "lng": 0.0,
                "messages": [{
                    "time": 1786000000.12,
                    "type": "HEARTBEAT",
                    "text": "LOITER armed=NO",
                }],
            },
        })

        encoded = compact_json_bytes(state)
        payload = json.loads(encoded)

        self.assertLessEqual(len(encoded.encode("utf-8")), 480)
        self.assertTrue(payload["position_observed"])
        self.assertEqual((payload["lat"], payload["lng"]), (0.0, 0.0))
        self.assertTrue(payload["aircraft"]["position_observed"])
        self.assertEqual(
            (payload["aircraft"]["lat"], payload["aircraft"]["lng"]),
            (0.0, 0.0),
        )
        self.assertEqual(payload["rover_tx_stage"], "failed")
        self.assertIn("GPS", payload["fault_text"])

    def test_compact_json_keeps_aircraft_message_text_readable(self):
        text = "心跳 GUIDED armed=NO sys=1/1"
        state = RoverTelemetry(
            lat=32.11956,
            lng=118.9584,
            mission_status="aircraft command aircraft_guided sent 2 MAVLink2 packets",
            fault_text="",
        ).data()
        state["aircraft"] = {
            "link_active": True,
            "last_seen_age_sec": 0.2,
            "last_remote": "192.168.4.1:14555 -> UDP 14560",
            "packets_received": 155,
            "bytes_received": 3966,
            "messages": [
                {"time": 1784799084.81 + idx, "type": "HEARTBEAT", "text": text}
                for idx in range(6)
            ],
        }

        payload = compact_json_bytes(state)

        self.assertLessEqual(len(payload.encode("utf-8")), 480)
        self.assertIn(text, payload)

    def test_rover_and_aircraft_transactions_are_separate_in_compact_state(self):
        telemetry = RoverTelemetry(
            rover_transaction_stage="executed",
            rover_transaction_id="rover-id",
            rover_transaction_pending=0,
            aircraft_transaction_stage="acknowledged",
            aircraft_transaction_id="aircraft-id",
            aircraft_transaction_pending=0,
            mission_status="rover waypoint complete",
            aircraft_mission_status="acknowledged",
        )

        data = telemetry.data()

        self.assertEqual(data["rover_tx_stage"], "executed")
        self.assertEqual(data["aircraft_tx_stage"], "acknowledged")
        self.assertEqual(data["mission_status"], "rover waypoint complete")
        self.assertEqual(data["aircraft_mission_status"], "acknowledged")
        self.assertNotIn("tx_stage", data)

    def test_command_receipt_never_exceeds_tuya_string_limit(self):
        state = RoverTelemetry(
            last_command="aircraft_loiter",
            aircraft_command_event="accepted",
            aircraft_command_event_id="805a77d6-a46b-4bee-a112-6fd70b21dc09",
        ).data()
        state.update(
            {
                "aircraft_link": True,
                "aircraft_age": 0.2,
                "aircraft_packets": 99999,
                "aircraft_msg": "HEARTBEAT 心跳 LOITER armed=NO sys=1/1",
                "aircraft_msg_time": 1784936725.48,
            }
        )

        payload = compact_json_bytes(state)

        self.assertLessEqual(len(payload.encode("utf-8")), 480)
        self.assertIn('"updated_at"', payload)
        self.assertIn('"last_command":"aircraft_loiter"', payload)

    def test_rover_receipt_cannot_evict_aircraft_heartbeat(self):
        state = RoverTelemetry(
            mission_status="mode hold sent",
            rover_transaction_stage="executed",
            rover_transaction_id="r" * 64,
            last_command="hold",
            fault_text="x" * 200,
        ).data()
        state.update({
            "aircraft_link": True,
            "aircraft_mode": "GUIDED",
            "aircraft_armed": False,
            "aircraft_altitude": 2.11,
            "aircraft_heading": 178.49,
            "aircraft": {
                "link_active": True,
                "messages": [{
                    "time": 1784950000.0,
                    "type": "HEARTBEAT",
                    "text": "GUIDED armed=NO",
                }],
            },
        })

        payload = json.loads(compact_json_bytes(state))

        self.assertEqual(payload["aircraft"]["mode"], "GUIDED")
        self.assertIs(payload["aircraft"]["armed"], False)
        self.assertTrue(payload["aircraft"]["messages"])

    def test_full_aircraft_telemetry_falls_back_to_essential_heartbeat(self):
        state = RoverTelemetry(
            lat=32.11956,
            lng=118.958406,
            last_command="aircraft_guided",
            aircraft_command_event="accepted",
            aircraft_command_event_id="a" * 64,
        ).data()
        state.update({
            "aircraft_link": True,
            "aircraft_mode": "LOITER",
            "aircraft_armed": False,
            "aircraft_battery_percent": 100,
            "aircraft_lat": 32.11961,
            "aircraft_lng": 118.95847,
            "aircraft_altitude": 7.024,
            "aircraft_ground_speed": 0.0,
            "aircraft_heading": 175.02,
            "aircraft_mission_status": "seq=0",
            "aircraft": {
                "link_active": True,
                "last_seen_age_sec": 0.41,
                "battery_percent": 100,
                "lat": 32.11961,
                "lng": 118.95847,
                "altitude": 7.024,
                "ground_speed": 0.0,
                "heading": 175.02,
                "mission_status": "seq=0",
                "messages": [
                    {"time": 1784954273.1, "type": "HEARTBEAT", "text": "LOITER armed=NO"},
                    {"time": 1784954274.36, "type": "RAW", "text": "收到数传 58B"},
                ],
            },
        })

        encoded = compact_json_bytes(state)
        payload = json.loads(encoded)

        self.assertLessEqual(len(encoded.encode("utf-8")), 480)
        self.assertEqual(payload["aircraft"]["mode"], "LOITER")
        self.assertEqual(payload["aircraft"]["messages"][0]["type"], "HEARTBEAT")
        self.assertEqual(payload["aircraft"]["messages"][0]["time"], 1784954273.1)
        self.assertEqual(payload["aircraft"]["altitude"], 7.02)
        self.assertEqual(payload["aircraft"]["heading"], 175.02)
        self.assertEqual(payload["aircraft"]["lat"], 32.11961)
        self.assertEqual(payload["aircraft"]["lng"], 118.95847)

    def test_aircraft_command_receipt_keeps_live_heartbeat_with_zero_positions(self):
        state = RoverTelemetry(
            lat=0.0,
            lng=0.0,
            position_observed=True,
            heading=272,
            flight_mode="hold",
            lte_rssi=-61,
            fc_link=True,
            last_command="aircraft_guided",
            aircraft_command_event="accepted",
            aircraft_command_event_id="a" * 36,
        ).data()
        state.update({
            "aircraft_link": True,
            "aircraft_mode": "GUIDED",
            "aircraft_armed": False,
            "aircraft_battery_percent": 100,
            "aircraft_lat": 0.0,
            "aircraft_lng": 0.0,
            "aircraft_position_observed": True,
            "aircraft_altitude": 1.92,
            "aircraft_ground_speed": 0.0,
            "aircraft_heading": 175.23,
            "aircraft": {
                "link_active": True,
                "last_seen_age_sec": 0.2,
                "position_observed": True,
                "messages": [{
                    "time": 1786005301.562,
                    "type": "HEARTBEAT",
                    "text": "HEARTBEAT GUIDED armed=NO",
                }],
            },
        })

        encoded = compact_json_bytes(state)
        payload = json.loads(encoded)

        self.assertLessEqual(len(encoded.encode("utf-8")), 480)
        self.assertTrue(payload["aircraft"]["link_active"])
        self.assertEqual(payload["aircraft"]["mode"], "GUIDED")
        self.assertIs(payload["aircraft"]["armed"], False)
        self.assertEqual(payload["aircraft"]["lat"], 0.0)
        self.assertTrue(payload["aircraft"]["position_observed"])
        self.assertEqual(
            payload["aircraft"]["messages"][0]["type"], "HEARTBEAT"
        )

    def test_aircraft_mission_transaction_survives_compact_cloud_state(self):
        state = RoverTelemetry(
            lat=32.11974,
            lng=118.95314,
            last_command="aircraft_mission_commit",
            aircraft_transaction_stage="VERIFIED",
            aircraft_transaction_id="a" * 64,
            aircraft_transaction_pending=0,
            aircraft_mission_status="execution_ready",
        ).data()
        state.update({
            "aircraft_link": True,
            "aircraft_mode": "GUIDED",
            "aircraft_armed": False,
            "aircraft_lat": 32.11981,
            "aircraft_lng": 118.95322,
            "aircraft_altitude": 5.3,
            "aircraft_heading": 181.96,
            "aircraft": {
                "link_active": True,
                "lat": 32.11981,
                "lng": 118.95322,
                "messages": [{
                    "time": 1784963000.1,
                    "type": "HEARTBEAT",
                    "text": "GUIDED armed=NO",
                }],
            },
        })

        encoded = compact_json_bytes(state)
        payload = json.loads(encoded)

        self.assertLessEqual(len(encoded.encode("utf-8")), 480)
        self.assertEqual(payload["aircraft_tx_stage"], "VERIFIED")
        self.assertEqual(payload["aircraft_tx_pending"], 0)
        self.assertEqual(payload["aircraft_mission_status"], "execution_ready")
        self.assertEqual(payload["lat"], 32.11974)
        self.assertEqual(payload["lng"], 118.95314)
        self.assertEqual(payload["aircraft"]["lat"], 32.11981)
        self.assertEqual(payload["aircraft"]["lng"], 118.95322)
        self.assertEqual(payload["aircraft"]["altitude"], 5.3)
        self.assertEqual(payload["aircraft"]["heading"], 181.96)

    def test_aircraft_terminal_event_updates_once_without_overwriting_rover_status(self):
        telemetry = RoverTelemetry(mission_status="rover executing")
        state = {
            "revision": 7,
            "stage": "acknowledged",
            "transaction_id": "aircraft-id",
            "pending": 0,
        }

        self.assertTrue(telemetry.apply_aircraft_transaction(state))
        self.assertEqual(telemetry.aircraft_mission_status, "acknowledged")
        self.assertEqual(telemetry.mission_status, "rover executing")
        telemetry.aircraft_mission_status = "operator note"
        self.assertFalse(telemetry.apply_aircraft_transaction(state))
        self.assertEqual(telemetry.aircraft_mission_status, "operator note")

    def test_rejected_aircraft_command_event_does_not_replace_active_transaction(self):
        telemetry = RoverTelemetry(
            aircraft_transaction_stage="awaiting_ack",
            aircraft_transaction_id="active-id",
            aircraft_transaction_pending=3,
        )

        telemetry.record_aircraft_command_event(
            "rejected-id", "rejected", "aircraft transaction already active"
        )

        self.assertEqual(telemetry.aircraft_transaction_stage, "awaiting_ack")
        self.assertEqual(telemetry.aircraft_transaction_id, "active-id")
        self.assertEqual(telemetry.aircraft_transaction_pending, 3)
        data = telemetry.data()
        self.assertEqual(data["aircraft_command_event_id"], "rejected-id")
        self.assertEqual(data["aircraft_command_event"], "rejected")
        self.assertEqual(
            data["aircraft_command_fault"],
            "aircraft transaction already active",
        )
        self.assertLessEqual(len(data["aircraft_command_event_id"]), 36)
        self.assertLessEqual(len(data["aircraft_command_fault"]), 120)

    def test_runtime_receipt_routes_aircraft_rejection_only_to_event_fields(self):
        telemetry = RoverTelemetry(
            aircraft_transaction_stage="awaiting_ack",
            aircraft_transaction_id="active-id",
            aircraft_transaction_pending=2,
        )

        record_command_receipt(
            telemetry,
            target="aircraft",
            command_id="rejected-id",
            accepted=False,
            stage="rejected",
            message="aircraft transaction already active",
        )

        self.assertEqual(telemetry.aircraft_transaction_id, "active-id")
        self.assertEqual(telemetry.aircraft_transaction_stage, "awaiting_ack")
        self.assertEqual(telemetry.aircraft_command_event_id, "rejected-id")
        self.assertEqual(telemetry.aircraft_command_event, "rejected")

    def test_slave_receipt_does_not_overwrite_rover_or_main_aircraft_fields(self):
        telemetry = RoverTelemetry(
            mission_status="rover ready",
            aircraft_transaction_stage="VERIFIED",
            aircraft_command_event="accepted",
        )

        record_command_receipt(
            telemetry,
            target="aircraft_2",
            command_id="slave-id",
            accepted=False,
            stage="FAILED",
            message="slave offline",
        )

        self.assertEqual(telemetry.mission_status, "rover ready")
        self.assertEqual(telemetry.aircraft_transaction_stage, "VERIFIED")
        self.assertEqual(telemetry.aircraft_command_event, "accepted")


if __name__ == "__main__":
    unittest.main()
