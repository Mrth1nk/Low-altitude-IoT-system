import unittest

from aircraft_agent.follow_target import (
    FollowTargetPublisher,
    PymavlinkFollowTargetEncoder,
)


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
        return b"\xfd-global-position"


class FollowTargetPublisherTests(unittest.TestCase):
    def test_real_encoder_always_emits_mavlink2_without_environment_switch(self):
        try:
            import pymavlink  # noqa: F401
        except ImportError:
            self.skipTest("pymavlink is only installed on the aircraft runtime")

        frame = PymavlinkFollowTargetEncoder().encode(
            timestamp_ms=42_000,
            lat=321_193_000,
            lon=1_189_530_000,
            alt=10_000,
            relative_alt=5_000,
            vx=0,
            vy=0,
            vz=0,
            hdg=9_000,
        )

        self.assertEqual(frame[0], 0xFD)

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
            relative_alt=5_600,
            vx=120,
            vy=-40,
            vz=10,
            hdg=12_345,
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

    def test_builds_global_position_with_relative_altitude_and_heading(self):
        publisher = self.make_publisher()
        self.observe_fresh_state(publisher)

        frame = publisher.next_frame()

        self.assertEqual(frame, b"\xfd-global-position")
        fields = self.encoder.calls[0]
        self.assertEqual(fields["message_id"], 33)
        self.assertEqual(fields["source_system"], 1)
        self.assertEqual(fields["source_component"], 1)
        self.assertEqual(fields["lat"], 321_193_000)
        self.assertEqual(fields["lon"], 1_189_530_000)
        self.assertEqual(fields["alt"], 123_400)
        self.assertEqual(fields["relative_alt"], 5_600)
        self.assertEqual((fields["vx"], fields["vy"], fields["vz"]), (120, -40, 10))
        self.assertEqual(fields["hdg"], 12_345)
        self.assertEqual(publisher.snapshot()["produced"], 1)

    def test_stale_position_or_heartbeat_stops_output(self):
        publisher = self.make_publisher()
        self.observe_fresh_state(publisher)
        self.clock.advance(1.51)

        self.assertIsNone(publisher.next_frame())
        self.assertEqual(publisher.snapshot()["stale"], 1)

    def test_missing_relative_altitude_or_heading_stops_output(self):
        publisher = self.make_publisher()
        publisher.observe(Message("HEARTBEAT"))
        publisher.observe(Message(
            "GLOBAL_POSITION_INT",
            time_boot_ms=42_000,
            lat=321_193_000,
            lon=1_189_530_000,
            alt=123_400,
            relative_alt=None,
            hdg=65_535,
        ))

        self.assertIsNone(publisher.next_frame())

        publisher.observe(Message(
            "GLOBAL_POSITION_INT",
            time_boot_ms=43_510,
            lat=321_193_000,
            lon=1_189_530_000,
            alt=123_400,
            relative_alt=5_600,
            hdg=12_345,
        ))
        self.assertIsNotNone(publisher.next_frame())

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
