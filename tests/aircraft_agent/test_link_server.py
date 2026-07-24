from pathlib import Path
import tempfile
import unittest
import uuid

from aircraft_agent.inbox import DurableInbox
from aircraft_agent.link_server import AircraftLinkServer
from aircraft_agent.optical_gate import OpticalGate
from aircraft_agent.state_store import AtomicJsonStore
from shared_protocol.auth import AuthenticatedDatagramCodec
from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame


KEY = b"aircraft-link-server-test-key!!!!"


class AircraftLinkServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.now = 100.0
        self.gate = OpticalGate(clock=lambda: self.now)
        self.inbox = DurableInbox(
            AtomicJsonStore(root / "inbox.json"), max_queue=1
        )
        self.server = AircraftLinkServer(
            self.inbox,
            self.gate,
            psk=KEY,
            auth_store=AtomicJsonStore(root / "auth.json"),
            max_wire_bytes=4096,
            max_payload=1024,
            clock=lambda: self.now,
            session_nonce=b"E" * 16,
            challenge=b"C" * 32,
        )
        self.rdk = AuthenticatedDatagramCodec(
            KEY, session_nonce=b"R" * 16
        )

    def tearDown(self):
        self.tmp.cleanup()

    def wire(self, frame):
        return self.rdk.seal(encode_frame(frame))

    def decode_responses(self, responses):
        return [
            decode_frame(self.rdk.open(datagram))
            for datagram in responses
        ]

    def authenticate(self):
        challenge = self.decode_responses([self.server.challenge_datagram()])[0]
        response = Frame(
            MessageType.AUTH_RESPONSE,
            0,
            1,
            uuid.UUID(int=0),
            {"challenge": challenge.payload["challenge"]},
        )
        frames = self.decode_responses(
            self.server.feed_bytes(self.wire(response))
        )
        self.assertIn(
            frames[0].message_type,
            (MessageType.STATUS, MessageType.LINK_BLOCKED),
        )
        self.assertEqual(self.server.peer_session_nonce, b"R" * 16)

    def test_requires_fresh_challenge_before_accepting_command(self):
        self.gate.set_locked(timestamp=self.now)
        command = Frame(
            MessageType.COMMAND,
            0,
            2,
            uuid.uuid4(),
            {"action": "guided", "parameters": {}},
        )

        unauthenticated = self.decode_responses(
            self.server.feed_bytes(self.wire(command))
        )
        self.assertEqual(
            [frame.message_type for frame in unauthenticated],
            [MessageType.AUTH_CHALLENGE],
        )
        self.assertIsNone(self.inbox.active)

        self.authenticate()
        accepted = self.decode_responses(
            self.server.feed_bytes(self.wire(command))
        )
        self.assertEqual(accepted[0].message_type, MessageType.ACK)
        self.assertEqual(self.inbox.active["command_id"], str(command.command_id))

    def test_new_peer_session_nonce_must_reauthenticate(self):
        self.gate.set_locked(timestamp=self.now)
        self.authenticate()
        restarted_rdk = AuthenticatedDatagramCodec(
            KEY, session_nonce=b"N" * 16
        )
        command = Frame(
            MessageType.COMMAND,
            0,
            3,
            uuid.uuid4(),
            {"action": "land", "parameters": {}},
        )

        responses = self.decode_responses(
            self.server.feed_bytes(restarted_rdk.seal(encode_frame(command)))
        )

        self.assertEqual(responses[0].message_type, MessageType.AUTH_CHALLENGE)
        self.assertIsNone(self.inbox.active)

    def test_blocked_command_produces_only_timestamped_link_blocked(self):
        self.authenticate()
        self.gate.set_blocked("beam_interrupted", timestamp=99.0)
        command = Frame(
            MessageType.COMMAND,
            0,
            4,
            uuid.uuid4(),
            {"action": "arm", "parameters": {}},
        )

        responses = self.decode_responses(
            self.server.feed_bytes(self.wire(command))
        )

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].message_type, MessageType.LINK_BLOCKED)
        self.assertEqual(responses[0].payload["timestamp"], 99.0)
        self.assertIsNone(self.inbox.active)

    def test_serial_stream_handles_split_and_concatenated_hmac_envelopes(self):
        self.gate.set_locked(timestamp=self.now)
        challenge = self.server.challenge_datagram()
        response = Frame(
            MessageType.AUTH_RESPONSE,
            0,
            5,
            uuid.UUID(int=0),
            {"challenge": "43" * 32},
        )
        packet = self.wire(response)

        self.assertEqual(self.server.feed_bytes(packet[:9]), [])
        first = self.server.feed_bytes(packet[9:] + packet)

        decoded = self.decode_responses(first)
        self.assertEqual(decoded[0].payload["state"], "authenticated")
        self.assertEqual(self.server.metrics["rejected_auth"], 1)
        self.assertTrue(challenge)

    def test_stream_resynchronizes_after_noise_and_rejects_oversized_envelope(self):
        self.gate.set_locked(timestamp=self.now)
        valid = self.wire(
            Frame(
                MessageType.AUTH_RESPONSE,
                0,
                6,
                uuid.UUID(int=0),
                {"challenge": "43" * 32},
            )
        )
        oversized_header = (
            b"LIA1" + b"Z" * 16 + (0).to_bytes(8, "big")
            + (5000).to_bytes(4, "big")
        )

        responses = self.server.feed_bytes(
            b"serial-noise" + oversized_header + valid
        )

        self.assertEqual(
            self.decode_responses(responses)[0].payload["state"],
            "authenticated",
        )
        self.assertGreaterEqual(self.server.metrics["rejected_size"], 1)

    def test_auth_replay_state_survives_server_restart(self):
        self.gate.set_locked(timestamp=self.now)
        packet = self.wire(
            Frame(
                MessageType.AUTH_RESPONSE,
                0,
                6,
                uuid.UUID(int=0),
                {"challenge": "43" * 32},
            )
        )
        self.server.feed_bytes(packet)
        root = Path(self.tmp.name)
        restarted = AircraftLinkServer(
            self.inbox,
            self.gate,
            psk=KEY,
            auth_store=AtomicJsonStore(root / "auth.json"),
            clock=lambda: self.now,
            session_nonce=b"E" * 16,
            challenge=b"D" * 32,
        )

        self.assertEqual(restarted.feed_bytes(packet), [])
        self.assertEqual(restarted.metrics["rejected_auth"], 1)

    def test_injected_serial_transport_reads_and_writes_envelopes(self):
        class Stream:
            def __init__(self):
                self.reads = []
                self.writes = []

            def read(self, size):
                del size
                return self.reads.pop(0) if self.reads else b""

            def write(self, data):
                self.writes.append(data)
                return len(data)

        stream = Stream()
        self.server.stream = stream
        self.gate.set_locked(timestamp=self.now)
        response = Frame(
            MessageType.AUTH_RESPONSE,
            0,
            9,
            uuid.UUID(int=0),
            {"challenge": "43" * 32},
        )
        packet = self.wire(response)
        stream.reads.extend([packet[:7], packet[7:]])

        self.assertEqual(self.server.pump_once(), 1)
        self.assertEqual(self.server.pump_once(), 1)
        decoded = self.decode_responses(stream.writes)
        self.assertIn(
            "authenticated", [frame.payload.get("state") for frame in decoded]
        )

    def test_idle_serial_pump_publishes_gate_status_without_extra_wiring(self):
        class Stream:
            def __init__(self):
                self.writes = []

            def read(self, size):
                del size
                return b""

            def write(self, data):
                self.writes.append(data)

        stream = Stream()
        self.server.stream = stream
        self.gate.set_locked(timestamp=self.now)

        sent = self.server.pump_once(snapshot={"mode": "GUIDED"})

        self.assertEqual(sent, 1)
        frame = self.decode_responses(stream.writes)[0]
        self.assertEqual(frame.message_type, MessageType.STATUS)
        self.assertIn("GUIDED", frame.payload["detail"])

    def test_authentication_while_blocked_does_not_leak_status_snapshot(self):
        challenge = self.decode_responses(
            [self.server.challenge_datagram()]
        )[0]
        response = Frame(
            MessageType.AUTH_RESPONSE,
            0,
            11,
            uuid.UUID(int=0),
            {"challenge": challenge.payload["challenge"]},
        )

        frames = self.decode_responses(
            self.server.feed_bytes(self.wire(response))
        )

        self.assertEqual(
            [frame.message_type for frame in frames],
            [MessageType.LINK_BLOCKED],
        )
        self.assertIn("timestamp", frames[0].payload)

    def test_poll_emits_only_gate_selected_status_at_one_hertz(self):
        blocked = self.decode_responses(
            self.server.poll_status({"mode": "GUIDED"})
        )
        self.assertEqual(
            [frame.message_type for frame in blocked],
            [MessageType.LINK_BLOCKED],
        )
        self.assertEqual(self.server.poll_status({"mode": "GUIDED"}), [])

        self.now = 101.0
        self.gate.set_locked(timestamp=self.now)
        locked = self.decode_responses(
            self.server.poll_status({"mode": "GUIDED", "armed": False})
        )
        self.assertEqual(
            [frame.message_type for frame in locked],
            [MessageType.STATUS],
        )
        self.assertEqual(locked[0].payload["timestamp"], 101.0)

    def test_complete_mission_returns_ack_then_staged_only_after_durable_commit(self):
        from tests.aircraft_agent.test_inbox import mission_frames

        self.gate.set_locked(timestamp=self.now)
        self.authenticate()
        responses = []
        for frame in mission_frames():
            responses = self.decode_responses(
                self.server.feed_bytes(self.wire(frame))
            )

        self.assertEqual(
            [frame.message_type for frame in responses],
            [MessageType.ACK, MessageType.STATUS],
        )
        self.assertEqual(responses[1].payload["state"], "MISSION_STAGED")
        self.assertEqual(self.inbox.active["kind"], "mission")

    def test_authenticated_liot_payload_over_limit_is_rejected(self):
        oversized = Frame(
            MessageType.COMMAND,
            0,
            10,
            uuid.uuid4(),
            {"action": "guided", "parameters": {"blob": "x" * 1500}},
        )

        responses = self.server.feed_bytes(
            self.rdk.seal(encode_frame(oversized, max_payload_length=4096))
        )

        self.assertEqual(responses, [])
        self.assertEqual(self.server.metrics["rejected_frame"], 1)


if __name__ == "__main__":
    unittest.main()
