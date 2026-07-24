import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
import uuid

from rdk_agent.aircraft_link import AircraftLink
from rdk_agent.aircraft_transport import AircraftTransport, legacy_gateway_ports
from rdk_agent.command_router import CloudCommand, CommandRouter, RuntimeCommandAPI
from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame


class AircraftTransportTests(unittest.TestCase):
    def setUp(self):
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer.bind(("127.0.0.1", 0))
        self.peer.settimeout(0.5)
        self.link = AircraftLink(max_attempts=2, retry_interval=0.02)
        self.transport = AircraftTransport(
            self.link,
            local_host="127.0.0.1",
            local_port=0,
            peer=self.peer.getsockname(),
        )
        self.command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

    def tearDown(self):
        self.transport.close()
        self.peer.close()

    def queue(self):
        self.link.execute(
            CloudCommand(
                self.command_id, time.time(), "aircraft", "guided", {}
            ),
            now=0.0,
        )

    def pump_until(self, predicate, now=0.001):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            self.transport.pump(now=now)
            if predicate():
                return
            time.sleep(0.001)
        self.fail("transport did not reach expected state")

    def test_due_frame_reaches_udp_peer_and_matching_ack_clears_pending(self):
        self.queue()
        self.transport.pump(now=0.0)
        data, sender = self.peer.recvfrom(4096)
        command = decode_frame(data)
        self.assertEqual(command.command_id, self.command_id)

        ack = Frame(
            MessageType.ACK,
            0,
            91,
            command.command_id,
            {"acked_sequence": command.sequence},
        )
        self.peer.sendto(encode_frame(ack), sender)
        self.pump_until(lambda: self.link.pending_count == 0)

        self.assertEqual(self.link.pending_count, 0)
        self.assertEqual(self.transport.transaction_state()["stage"], "acknowledged")

    def test_mismatched_ack_does_not_clear_pending(self):
        self.queue()
        self.transport.pump(now=0.0)
        data, sender = self.peer.recvfrom(4096)
        command = decode_frame(data)
        ack = Frame(
            MessageType.ACK,
            0,
            92,
            uuid.uuid4(),
            {"acked_sequence": command.sequence},
        )
        self.peer.sendto(encode_frame(ack), sender)
        time.sleep(0.005)
        self.transport.pump(now=0.001)

        self.assertEqual(self.link.pending_count, 1)

        wrong_sequence = Frame(
            MessageType.ACK,
            0,
            94,
            command.command_id,
            {"acked_sequence": command.sequence + 1},
        )
        self.peer.sendto(encode_frame(wrong_sequence), sender)
        time.sleep(0.005)
        self.transport.pump(now=0.002)

        self.assertEqual(self.link.pending_count, 1)

    def test_link_blocked_status_reaches_router(self):
        blocked = Frame(
            MessageType.LINK_BLOCKED,
            0,
            3,
            uuid.uuid4(),
            {"reason": "optical_lost"},
        )
        self.peer.sendto(encode_frame(blocked), self.transport.local_address)
        self.pump_until(lambda: self.transport.status()["link_detail"] == "optical_lost")
        rover = lambda command: None
        router = CommandRouter(
            rover,
            self.link,
            optical_state=self.transport.optical_state,
        )

        self.assertEqual(self.transport.optical_state(), "blocked")
        self.assertEqual(
            self.transport.status()["link_detail"], "optical_lost"
        )

    def test_retries_exhaust_into_transaction_error(self):
        self.queue()
        self.transport.pump(now=0.0)
        self.peer.recvfrom(4096)
        self.transport.pump(now=0.02)
        self.peer.recvfrom(4096)
        self.transport.pump(now=0.04)

        state = self.transport.transaction_state()
        self.assertEqual(state["stage"], "retry_exhausted")
        self.assertEqual(state["pending"], 0)
        self.assertIn("sequence", state["error"])

    def test_udp_send_error_is_exposed_as_transaction_error(self):
        self.queue()
        real_socket = self.transport._socket

        class FailingSocket:
            def recvfrom(self, size):
                raise BlockingIOError()

            def sendto(self, payload, peer):
                raise OSError("network down")

            def getsockname(self):
                return real_socket.getsockname()

            def close(self):
                real_socket.close()

        self.transport._socket = FailingSocket()
        state = self.transport.pump(now=0.0)

        self.assertEqual(state["stage"], "transport_error")
        self.assertIn("network down", state["error"])

    def test_nack_updates_transaction_error_and_clears_matching_frame(self):
        self.queue()
        self.transport.pump(now=0.0)
        data, sender = self.peer.recvfrom(4096)
        command = decode_frame(data)
        nack = Frame(
            MessageType.NACK,
            0,
            93,
            command.command_id,
            {"acked_sequence": command.sequence, "reason": "mission_denied"},
        )
        self.peer.sendto(encode_frame(nack), sender)
        self.pump_until(lambda: self.link.pending_count == 0)

        state = self.transport.transaction_state()
        self.assertEqual(state["stage"], "nacked")
        self.assertEqual(state["error"], "mission_denied")

    def test_runtime_api_accepts_complete_normalized_mission(self):
        class Locked:
            @staticmethod
            def optical_state():
                return "locked"

        router = CommandRouter(
            lambda command: None,
            self.link,
            Locked.optical_state,
            clock=lambda: 1_800_000_000.0,
        )
        api = RuntimeCommandAPI(router, clock=lambda: 1_800_000_000.0)
        result = api.submit_mission(
            {
                "mission_id": "task11",
                "items": [
                    {
                        "lat": 32.1,
                        "lon": 118.9,
                        "alt": 20,
                        "command": 16,
                        "frame": 6,
                    }
                ],
            },
            command_id=self.command_id,
        )

        self.assertEqual(result["stage"], "staged")
        self.assertEqual(self.link.pending_count, 3)


class StartupImportTests(unittest.TestCase):
    def test_legacy_gateway_cannot_share_reliable_transport_port(self):
        self.assertEqual(legacy_gateway_ports({}, 14560), [14550])
        with self.assertRaisesRegex(ValueError, "must not share"):
            legacy_gateway_ports({"aircraft_udp_ports": [14560, 14550]}, 14560)

    def test_start_script_import_check_succeeds_from_rdk_directory(self):
        repo = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["IMPORT_CHECK_ONLY"] = "1"

        result = subprocess.run(
            ["bash", "start_rover_stack.sh"],
            cwd=repo / "rdk_agent",
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime imports ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
