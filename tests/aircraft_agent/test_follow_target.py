import unittest

from aircraft_agent.follow_target import FollowTargetPublisher


class Clock:
    def __init__(self, value=100.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class Message:
    def __init__(self, kind, *, system=1, **fields):
        self.kind = kind
        self.system = system
        for name, value in fields.items():
            setattr(self, name, value)

    def get_type(self):
        return self.kind

    def get_srcSystem(self):
        return self.system


class Encoder:
    def __init__(self):
        self.calls = []

    def encode(self, **fields):
        self.calls.append(fields)
        return b"\xfd-follow-target"


class FollowTargetPublisherTests(unittest.TestCase):
    def make_publisher(self):
        self.clock = Clock()
        self.encoder = Encoder()
        return FollowTargetPublisher(encoder=self.encoder, clock=self.clock)

    def observe_fresh_state(self, publisher):
        publisher.observe(Message("HEARTBEAT"))
        publisher.observe(Message(
            "GLOBAL_POSITION_INT",
            time_boot_ms=42_000,
            lat=321_193_000,
            lon=1_189_530_000,
            alt=123_400,
            vx=120,
            vy=-40,
            vz=10,
        ))
        publisher.observe(Message(
            "ATTITUDE",
            roll=0.1,
            pitch=-0.2,
            yaw=1.0,
            rollspeed=0.01,
            pitchspeed=0.02,
            yawspeed=0.03,
        ))

    def test_builds_mavlink2_follow_target_from_fresh_leader_state(self):
        publisher = self.make_publisher()
        self.observe_fresh_state(publisher)

        frame = publisher.next_frame()

        self.assertEqual(frame, b"\xfd-follow-target")
        fields = self.encoder.calls[0]
        self.assertEqual(fields["message_id"], 144)
        self.assertEqual(fields["source_system"], 1)
        self.assertEqual(fields["source_component"], 1)
        self.assertEqual(fields["lat"], 321_193_000)
        self.assertEqual(fields["lon"], 1_189_530_000)
        self.assertAlmostEqual(fields["alt"], 123.4)
        self.assertEqual(fields["vel"], (1.2, -0.4, 0.1))
        self.assertEqual(len(fields["attitude_q"]), 4)
        self.assertEqual(publisher.snapshot()["produced"], 1)

    def test_stale_position_or_heartbeat_stops_output(self):
        publisher = self.make_publisher()
        self.observe_fresh_state(publisher)
        self.clock.advance(1.51)

        self.assertIsNone(publisher.next_frame())
        self.assertEqual(publisher.snapshot()["stale"], 1)

    def test_wrong_system_and_rate_limit_are_rejected(self):
        publisher = self.make_publisher()
        publisher.observe(Message("HEARTBEAT", system=2))
        publisher.observe(Message(
            "GLOBAL_POSITION_INT",
            system=2,
            lat=321_193_000,
            lon=1_189_530_000,
            alt=100_000,
        ))
        self.assertIsNone(publisher.next_frame())

        self.observe_fresh_state(publisher)
        self.assertIsNotNone(publisher.next_frame())
        self.clock.advance(0.05)
        self.assertIsNone(publisher.next_frame())
        self.clock.advance(0.05)
        self.assertIsNotNone(publisher.next_frame())


if __name__ == "__main__":
    unittest.main()
