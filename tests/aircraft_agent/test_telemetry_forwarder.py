import unittest

from aircraft_agent.main import MavlinkTelemetryForwarder


class FakeMessage:
    def __init__(self, kind, frame):
        self.kind = kind
        self.frame = frame

    def get_type(self):
        return self.kind

    def get_msgbuf(self):
        return self.frame


class FakeStream:
    def __init__(self):
        self.frames = []

    def write(self, frame):
        self.frames.append(bytes(frame))
        return len(frame)


class MavlinkTelemetryForwarderTests(unittest.TestCase):
    def test_forwards_allowed_mavlink_frame_only_when_optical_path_is_locked(self):
        forwarder = MavlinkTelemetryForwarder()
        stream = FakeStream()
        frame = b"\xfd\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00"

        forwarder.observe(FakeMessage("HEARTBEAT", frame))
        forwarder.drain_to(stream, allowed=False)
        self.assertEqual(stream.frames, [])

        forwarder.observe(FakeMessage("HEARTBEAT", frame))
        forwarder.drain_to(stream, allowed=True)
        self.assertEqual(stream.frames, [frame])
        self.assertEqual(forwarder.snapshot()["written"], 1)

    def test_drops_non_mavlink_and_unlisted_messages(self):
        forwarder = MavlinkTelemetryForwarder()
        stream = FakeStream()
        forwarder.observe(FakeMessage("HEARTBEAT", b"not-mavlink"))
        forwarder.observe(FakeMessage("ATTITUDE", b"\xfd\x00"))
        forwarder.drain_to(stream, allowed=True)
        self.assertEqual(stream.frames, [])

    def test_queues_generated_mavlink2_frame_on_existing_stream(self):
        forwarder = MavlinkTelemetryForwarder()
        stream = FakeStream()
        frame = b"\xfd\x00\x00\x00\x00\x01\x01\x90\x00\x00\x00\x00"

        self.assertTrue(forwarder.enqueue(frame))
        forwarder.drain_to(stream, allowed=True)

        self.assertEqual(stream.frames, [frame])

    def test_follow_target_bypasses_only_the_optical_telemetry_gate(self):
        forwarder = MavlinkTelemetryForwarder()
        stream = FakeStream()
        heartbeat = b"\xfd\x00\x00\x00\x00\x01\x01\x00\x00\x00\x00\x00"
        follow = b"\xfd\x00\x00\x00\x00\x01\x01\x90\x00\x00\x00\x00"

        forwarder.enqueue(heartbeat)
        forwarder.enqueue(follow, bypass_gate=True)
        forwarder.drain_to(stream, allowed=False)

        self.assertEqual(stream.frames, [follow])
        self.assertEqual(forwarder.snapshot()["bypass_written"], 1)


if __name__ == "__main__":
    unittest.main()
