import unittest

from shared_protocol.mavlink_extension import (
    unwrap_liot_frame,
    wrap_liot_frame,
)


class MavlinkExtensionTests(unittest.TestCase):
    def test_round_trip_preserves_liot_bytes(self):
        payload = b"LIOT" + bytes(range(180))
        self.assertEqual(unwrap_liot_frame(wrap_liot_frame(payload)), payload)

    def test_bad_checksum_is_rejected(self):
        packet = bytearray(wrap_liot_frame(b"LIOT-test"))
        packet[-1] ^= 1
        self.assertIsNone(unwrap_liot_frame(packet))

    def test_payload_over_249_bytes_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            wrap_liot_frame(b"x" * 250)
