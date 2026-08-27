import unittest

from rdk_agent.aircraft_gateway import AircraftGatewayState


class AircraftGatewayStateTests(unittest.TestCase):
    def test_relay_only_follow_frame_does_not_refresh_optical_link(self):
        state = AircraftGatewayState()
        follow = bytes([
            0xFD, 0, 0, 0, 0, 1, 1,
            144, 0, 0,
            0, 0,
        ])

        parsed = state.record_packet(
            14560,
            follow,
            ("192.168.4.1", 14555),
            touch_unparsed=False,
        )

        snapshot = state.snapshot()
        self.assertFalse(parsed)
        self.assertFalse(snapshot["link_active"])
        self.assertEqual(snapshot["packets_received"], 1)
        self.assertEqual(snapshot["messages"], [])


if __name__ == "__main__":
    unittest.main()
