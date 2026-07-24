import unittest
from pathlib import Path


class NetworkSwitchInstallTests(unittest.TestCase):
    def test_phone_profile_with_spaces_is_quoted_for_systemd(self):
        script = Path("rdk_agent/install_network_switch_service.sh").read_text()

        self.assertIn(
            'Environment="PHONE_WIFI_PROFILE=Mr.think的Mate 70 Pro+"',
            script,
        )
        self.assertNotIn(
            "\nEnvironment=PHONE_WIFI_PROFILE=Mr.think的Mate 70 Pro+\n",
            script,
        )

    def test_switch_script_uses_valid_iproute_interface_syntax(self):
        script = Path("rdk_agent/switch_network_mode.sh").read_text()

        self.assertIn('ip -br addr show dev "$WIFI_IFACE"', script)


if __name__ == "__main__":
    unittest.main()
