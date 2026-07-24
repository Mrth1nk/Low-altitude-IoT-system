import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from aircraft_agent.inbox import DurableInbox
from aircraft_agent.link_server import AircraftLinkServer
from aircraft_agent.mission_worker import AircraftMissionWorker
from aircraft_agent.optical_gate import OpticalGate
from aircraft_agent.state_store import AtomicJsonStore
from aircraft_agent.telemetry import AircraftTelemetry
from rdk_agent.aircraft_link import AircraftLink
from rdk_agent.command_router import CloudCommand, CommandRouter
from shared_protocol.auth import AuthenticatedDatagramCodec
from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame


KEY = b"end-to-end-integration-key!!!!"


class FakeFlightController:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.now = 200.0

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def send(self, message_type, **fields):
        self.sent.append((message_type, fields))

    def receive(self, accepted, timeout):
        del timeout
        self.now += 0.01
        while self.messages:
            message = self.messages.pop(0)
            if message["type"] in accepted:
                return message
        return None


def ready_telemetry(clock):
    telemetry = AircraftTelemetry(clock=clock)
    for message in (
        {"type": "GPS_RAW_INT", "fix_type": 3, "satellites_visible": 10},
        {
            "type": "GLOBAL_POSITION_INT",
            "lat": 321197400,
            "lon": 1189531400,
        },
        {
            "type": "HOME_POSITION",
            "latitude": 321197400,
            "longitude": 1189531400,
            "altitude": 0,
        },
        {"type": "EKF_STATUS_REPORT", "flags": 17},
    ):
        telemetry.update(message)
    return telemetry


def fc_handshake(record):
    home = {
        "type": "MISSION_ITEM_INT",
        "seq": 0,
        "frame": 0,
        "command": 16,
        "x": 321197400,
        "y": 1189531400,
        "z": 0.0,
        "param1": 0.0,
        "param2": 0.0,
        "param3": 0.0,
        "param4": 0.0,
        "autocontinue": 1,
        "mission_type": 0,
    }
    readback = [home]
    for sequence, item in enumerate(record["items"], start=1):
        readback.append(
            {
                "type": "MISSION_ITEM_INT",
                "seq": sequence,
                "frame": 3 if item["frame"] == 6 else item["frame"],
                "command": item["command"],
                "x": int(round(item["lat"] * 1e7)),
                "y": int(round(item["lon"] * 1e7)),
                "z": item["alt"],
                "param1": item["param1"],
                "param2": item["param2"],
                "param3": item["param3"],
                "param4": item["param4"],
                "autocontinue": int(item["autocontinue"]),
                "mission_type": 0,
            }
        )
    requests = [
        {"type": "MISSION_REQUEST_INT", "seq": sequence, "mission_type": 0}
        for sequence in (2, 0, 1)
    ]
    return [
        {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
        *requests,
        {"type": "MISSION_ACK", "result": 0, "mission_type": 0},
        {"type": "MISSION_COUNT", "count": len(readback), "mission_type": 0},
        *readback,
    ]


class EndToEndMissionTests(unittest.TestCase):
    def test_ground_payload_reaches_verified_fake_fc_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = 1_800_000_000.0
            link = AircraftLink()
            router = CommandRouter(
                rover_executor=object(),
                aircraft_link=link,
                optical_state=lambda: "locked",
                clock=lambda: now,
            )
            command_id = uuid.UUID("11223344-5566-7788-99aa-bbccddeeff00")
            ground_payload = {
                "command_id": {"value": str(command_id)},
                "source_timestamp": {"value": now},
                "target": {"value": "aircraft"},
                "action": {"value": "mission"},
                "payload": {
                    "value": {
                        "mission_id": "e2e-demo",
                        "items": [
                            {
                                "lat": 32.1197,
                                "lon": 118.9531,
                                "alt": 20.0,
                                "command": 16,
                                "frame": 6,
                                "param1": 0.0,
                                "param2": 0.0,
                                "param3": 0.0,
                                "param4": 0.0,
                                "autocontinue": True,
                            },
                            {
                                "lat": 32.1200,
                                "lon": 118.9540,
                                "alt": 20.0,
                                "command": 16,
                                "frame": 6,
                                "param1": 0.0,
                                "param2": 0.0,
                                "param3": 0.0,
                                "param4": 0.0,
                                "autocontinue": True,
                            },
                        ],
                    }
                },
            }
            command = CloudCommand.from_cloud(ground_payload, clock=lambda: now)
            router.route(command)

            gate = OpticalGate(clock=lambda: now)
            gate.set_locked(timestamp=now)
            inbox = DurableInbox(AtomicJsonStore(root / "inbox.json"))
            server = AircraftLinkServer(
                inbox,
                gate,
                psk=KEY,
                auth_store=AtomicJsonStore(root / "elf-auth.json"),
                clock=lambda: now,
                session_nonce=b"E" * 16,
                challenge=b"C" * 32,
            )
            rdk_codec = AuthenticatedDatagramCodec(
                KEY, session_nonce=b"R" * 16
            )

            challenge = decode_frame(rdk_codec.open(server.challenge_datagram()))
            auth = Frame(
                MessageType.AUTH_RESPONSE,
                0,
                900,
                uuid.UUID(int=0),
                {"challenge": challenge.payload["challenge"]},
            )
            server.feed_bytes(rdk_codec.seal(encode_frame(auth)))

            for raw in link.due_bytes(0.0):
                replies = server.feed_bytes(rdk_codec.seal(raw))
                for reply in replies:
                    response = decode_frame(rdk_codec.open(reply))
                    if response.message_type in (
                        MessageType.ACK,
                        MessageType.NACK,
                    ):
                        link.accept_response(response)

            self.assertEqual(link.transaction_state()["stage"], "acknowledged")
            staged = inbox.active
            self.assertEqual(staged["stage"], "MISSION_STAGED")
            expected_checksum = hashlib.sha256(
                json.dumps(
                    staged["items"],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            self.assertEqual(staged["checksum"], expected_checksum)

            fc = FakeFlightController(fc_handshake(staged))
            telemetry = ready_telemetry(lambda: fc.now)
            worker = AircraftMissionWorker(
                fc,
                telemetry,
                clock=lambda: fc.now,
                timeout=0.05,
                operation_timeout=2.0,
            )
            result = worker.execute(staged, start_auto=True)
            self.assertTrue(result.verified)
            self.assertTrue(result.execution_ready)
            self.assertIn(("SET_MODE", {"mode": "AUTO"}), fc.sent)
            self.assertEqual(
                fc.sent[-2],
                ("MISSION_ACK", {"result": 0, "mission_type": 0}),
            )

            persisted = inbox.finish_active(
                "VERIFIED",
                "fake FC upload/readback matched",
                verified=True,
                execution_ready=True,
            )
            self.assertEqual(persisted["stage"], "VERIFIED")
            status = server.poll_transaction_status()
            decoded_status = [
                decode_frame(rdk_codec.open(packet)) for packet in status
            ]
            self.assertEqual(decoded_status[0].payload["state"], "VERIFIED")
            self.assertIsNone(inbox.active)


if __name__ == "__main__":
    unittest.main()
