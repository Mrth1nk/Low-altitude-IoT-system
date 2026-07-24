import hashlib
import hmac
import unittest

import tuya_rover_agent
from tuya_auth import build_tuya_credentials, make_topic


class TuyaAuthTests(unittest.TestCase):
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
