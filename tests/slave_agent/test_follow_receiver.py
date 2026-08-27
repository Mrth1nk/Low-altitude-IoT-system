import unittest

from slave_agent.follow_receiver import FollowTargetReceiver


def mavlink2_frame(*, message_id=33, source_system=1, payload=b"target"):
    return bytes((
        0xFD, len(payload), 0, 0, 9, source_system, 1,
        message_id & 0xFF,
        (message_id >> 8) & 0xFF,
        (message_id >> 16) & 0xFF,
    )) + payload + b"\x00\x00"


class Socket:
    def __init__(self):
        self.incoming = []
        self.bound = None
        self.blocking = None
        self.closed = False

    def bind(self, address):
        self.bound = address

    def setblocking(self, value):
        self.blocking = value

    def recvfrom(self, _size):
        if not self.incoming:
            raise BlockingIOError
        return self.incoming.pop(0)

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.raw_writes = []

    def write_raw(self, frame):
        self.raw_writes.append(bytes(frame))
        return len(frame)


class FollowTargetReceiverTests(unittest.TestCase):
    def setUp(self):
        self.sock = Socket()
        self.session = Session()
        self.receiver = FollowTargetReceiver(
            self.session,
            local_host="0.0.0.0",
            local_port=14630,
            rover_ip="192.168.4.2",
            sock=self.sock,
        )

    def test_injects_valid_frame_through_existing_session(self):
        frame = mavlink2_frame()
        self.sock.incoming.append((frame, ("192.168.4.2", 49152)))

        self.assertEqual(self.receiver.run_once(), 1)

        self.assertEqual(self.session.raw_writes, [frame])
        self.assertEqual(self.sock.bound, ("0.0.0.0", 14630))
        self.assertFalse(self.sock.blocking)

    def test_rejects_wrong_peer_message_source_and_nonexact_datagram(self):
        self.sock.incoming.extend([
            (mavlink2_frame(), ("192.168.4.9", 49152)),
            (mavlink2_frame(source_system=2), ("192.168.4.2", 49152)),
            (mavlink2_frame(message_id=144), ("192.168.4.2", 49152)),
            (mavlink2_frame() + b"tail", ("192.168.4.2", 49152)),
        ])

        self.assertEqual(self.receiver.run_once(), 0)
        self.assertEqual(self.session.raw_writes, [])
        self.assertEqual(self.receiver.snapshot()["rejected"], 4)


if __name__ == "__main__":
    unittest.main()
