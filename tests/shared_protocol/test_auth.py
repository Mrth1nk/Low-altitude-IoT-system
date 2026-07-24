import unittest

from shared_protocol.auth import (
    AuthError,
    AuthenticatedDatagramCodec,
    ReplayError,
)


class AuthenticatedDatagramCodecTests(unittest.TestCase):
    def setUp(self):
        self.key = b"competition-test-key-32-bytes!!"
        self.sender = AuthenticatedDatagramCodec(
            self.key, session_nonce=b"A" * 16
        )
        self.receiver = AuthenticatedDatagramCodec(
            self.key, session_nonce=b"B" * 16, replay_window=8, max_sessions=2
        )

    def test_round_trip_authenticates_payload_without_exposing_key(self):
        datagram = self.sender.seal(b"LIOT frame")

        self.assertEqual(self.receiver.open(datagram), b"LIOT frame")
        self.assertNotIn(self.key, datagram)

    def test_bad_mac_and_unauthenticated_payload_are_rejected(self):
        datagram = bytearray(self.sender.seal(b"payload"))
        datagram[-1] ^= 0x01

        with self.assertRaises(AuthError):
            self.receiver.open(bytes(datagram))
        with self.assertRaises(AuthError):
            self.receiver.open(b"raw LIOT frame")

    def test_replay_is_rejected_but_bounded_out_of_order_is_accepted(self):
        first = self.sender.seal(b"first")
        second = self.sender.seal(b"second")
        third = self.sender.seal(b"third")

        self.assertEqual(self.receiver.open(third), b"third")
        self.assertEqual(self.receiver.open(second), b"second")
        self.assertEqual(self.receiver.open(first), b"first")
        with self.assertRaises(ReplayError):
            self.receiver.open(second)

    def test_old_counter_and_excess_sessions_are_bounded(self):
        packets = [self.sender.seal(str(index).encode()) for index in range(10)]
        self.receiver.open(packets[-1])
        with self.assertRaises(ReplayError):
            self.receiver.open(packets[0])

        for nonce in (b"C" * 16, b"D" * 16, b"E" * 16):
            other = AuthenticatedDatagramCodec(self.key, session_nonce=nonce)
            self.receiver.open(other.seal(nonce))
        self.assertLessEqual(self.receiver.session_count, 2)

    def test_requires_nontrivial_psk_and_fixed_nonce_size(self):
        with self.assertRaises(ValueError):
            AuthenticatedDatagramCodec(b"short")
        with self.assertRaises(ValueError):
            AuthenticatedDatagramCodec(self.key, session_nonce=b"short")


if __name__ == "__main__":
    unittest.main()
