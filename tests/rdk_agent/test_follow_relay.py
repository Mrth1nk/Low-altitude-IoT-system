import unittest

from rdk_agent.follow_relay import FollowTargetRelay


def mavlink2_frame(*, message_id=144, source_system=1, payload=b"target"):
    return bytes((
        0xFD,
        len(payload),
        0,
        0,
        7,
        source_system,
        1,
        message_id & 0xFF,
        (message_id >> 8) & 0xFF,
        (message_id >> 16) & 0xFF,
    )) + payload + b"\x00\x00"


class Socket:
    def __init__(self, *_args):
        self.sent = []
        self.closed = False

    def sendto(self, payload, peer):
        self.sent.append((bytes(payload), peer))
        return len(payload)

    def close(self):
        self.closed = True


class FollowTargetRelayTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.socket = Socket()
        self.relay = FollowTargetRelay(
            peer=("192.168.4.3", 14630),
            socket_factory=lambda *_args: self.socket,
            clock=lambda: self.now,
        )

    def tearDown(self):
        self.relay.close()

    def test_relays_only_complete_follow_target_from_system_one(self):
        frame = mavlink2_frame()

        count = self.relay.observe(frame, observed_at=self.now)

        self.assertEqual(count, 1)
        self.assertEqual(self.socket.sent, [(frame, ("192.168.4.3", 14630))])
        self.assertEqual(self.relay.snapshot()["sent"], 1)

    def test_reassembles_split_frame_and_skips_noise(self):
        frame = mavlink2_frame(payload=b"split-target")

        self.assertEqual(self.relay.observe(b"noise" + frame[:8]), 0)
        self.assertEqual(self.relay.observe(frame[8:]), 1)

        self.assertEqual(self.socket.sent[0][0], frame)

    def test_rejects_wrong_message_source_version_and_stale_observation(self):
        self.relay.observe(mavlink2_frame(source_system=2))
        self.relay.observe(mavlink2_frame(message_id=33))
        self.relay.observe(b"\xfe\x00\x01\x01\x01\x90\x00\x00")
        self.relay.observe(mavlink2_frame(), observed_at=self.now - 1.51)

        self.assertEqual(self.socket.sent, [])
        snapshot = self.relay.snapshot()
        self.assertGreaterEqual(snapshot["rejected"], 3)
        self.assertEqual(snapshot["stale"], 1)


if __name__ == "__main__":
    unittest.main()
