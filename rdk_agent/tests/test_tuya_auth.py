import hashlib
import hmac
import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import tuya_rover_agent
from rover_state import RoverTelemetry
from tuya_auth import build_tuya_credentials, make_topic


class TuyaAuthTests(unittest.TestCase):

    def test_slave_node_is_disabled_by_default_and_can_be_enabled_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"device_id": "dev", "device_secret": "secret"}))
            with patch.dict(os.environ, {}, clear=True):
                disabled = tuya_rover_agent.load_config(path)
            with patch.dict(os.environ, {"SLAVE_NODE_ENABLED": "1"}, clear=True):
                enabled = tuya_rover_agent.load_config(path)

        self.assertFalse(disabled["slave_node_enabled"])
        self.assertTrue(enabled["slave_node_enabled"])
        self.assertEqual(enabled["slave_peer_host"], "192.168.4.3")
        self.assertEqual(enabled["slave_local_port"], 14610)
        self.assertEqual(enabled["slave_peer_port"], 14620)
        self.assertEqual(enabled["slave_max_attempts"], 6)

    def test_one_property_report_contains_both_states_without_extra_report(self):
        class Slave:
            def snapshot(self):
                return {
                    "updated_at": 1000.0, "online": True, "fc_connected": True,
                    "blocked": False, "link_state": "ONLINE", "mode": "GUIDED",
                    "armed": False, "lat": 0.0, "lon": 0.0,
                    "position_observed": True, "altitude": 0.0, "speed": 0.0,
                    "heading": 0.0, "battery": 100, "mission_stage": "IDLE",
                    "mission_id": "", "fault": "",
                    "event": {"timestamp": 1000.0, "sequence": 1, "type": "HEARTBEAT", "text": "ok"},
                }

        payload = tuya_rover_agent.build_property_report(
            RoverTelemetry(), {"aircraft_link": True}, Slave()
        )

        self.assertIn("rover_state", payload["data"])
        self.assertIn("slave_state", payload["data"])
        self.assertEqual(
            payload["data"]["rover_state"]["time"],
            payload["data"]["slave_state"]["time"],
        )

    def test_slave_snapshot_failure_isolated_from_existing_report(self):
        class BrokenSlave:
            def snapshot(self):
                raise OSError("slave unavailable")

        telemetry = RoverTelemetry()
        payload = tuya_rover_agent.build_property_report(
            telemetry, {"aircraft_link": True}, BrokenSlave()
        )

        self.assertIn("rover_state", payload["data"])
        self.assertNotIn("slave_state", payload["data"])
    def test_build_tuya_credentials_uses_tuya_username_shape_and_hmac(self):
        creds = build_tuya_credentials("dev001", "secret", 1700000000)
        expected_username = "dev001|signMethod=hmacSha256,timestamp=1700000000,secureMode=1,accessType=1"
        expected_content = "deviceId=dev001,timestamp=1700000000,secureMode=1,accessType=1"
        expected_password = hmac.new(b"secret", expected_content.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(creds["client_id"], "dev001")
        self.assertEqual(creds["username"], expected_username)
        self.assertEqual(creds["password"], expected_password)

    def test_make_topic_uses_tylink_device_prefix(self):
        self.assertEqual(make_topic("dev001", "thing/property/report"), "tylink/dev001/thing/property/report")

    def test_mqtt_client_uses_tuyalink_prefixed_client_id(self):
        original_client = tuya_rover_agent.mqtt.Client
        seen = {}

        class FakeClient:
            def __init__(self, client_id, protocol):
                seen["client_id"] = client_id
                seen["protocol"] = protocol

            def username_pw_set(self, username, password):
                pass

            def tls_set(self, **kwargs):
                pass

            def enable_logger(self):
                pass

        try:
            tuya_rover_agent.mqtt.Client = FakeClient
            tuya_rover_agent.mqtt_client({"device_id": "dev001", "device_secret": "secret"})
        finally:
            tuya_rover_agent.mqtt.Client = original_client

        self.assertEqual(seen["client_id"], "tuyalink_dev001")

    def test_start_aircraft_gateway_uses_default_udp_ports(self):
        calls = []
        original_gateway = tuya_rover_agent.start_aircraft_gateway
        try:
            tuya_rover_agent.start_aircraft_gateway = lambda ports, path: calls.append((ports, path)) or "gateway"

            gateway = tuya_rover_agent.start_aircraft_state_gateway({})
        finally:
            tuya_rover_agent.start_aircraft_gateway = original_gateway

        self.assertEqual(gateway, "gateway")
        self.assertEqual(calls[0][0], [14550])
        self.assertEqual(calls[0][1], tuya_rover_agent.AIRCRAFT_STATE_PATH)


if __name__ == "__main__":
    unittest.main()
