from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import uuid

from aircraft_agent.inbox import DurableInbox
from aircraft_agent.link_server import AircraftLinkServer
from aircraft_agent.optical_gate import OpticalGate
from aircraft_agent.state_store import AtomicJsonStore
from rdk_agent.aircraft_link import AircraftLink
from rdk_agent.command_router import CloudCommand
from shared_protocol.auth import AuthenticatedDatagramCodec
from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame


ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "ops" / "demo_preflight.sh"
KEY = b"integration-link-key-32-bytes!!!"


def mission_payload():
    return {
        "mission_id": "fault-injection-demo",
        "items": [
            {
                "lat": 32.1197,
                "lon": 118.9531,
                "alt": 20.0,
                "command": 16,
                "frame": 6,
                "autocontinue": True,
            },
            {
                "lat": 32.1200,
                "lon": 118.9540,
                "alt": 20.0,
                "command": 16,
                "frame": 6,
                "autocontinue": True,
            },
        ],
    }


class LinkHarness:
    def __init__(self, root):
        self.now = 100.0
        self.gate = OpticalGate(clock=lambda: self.now)
        self.gate.set_locked(timestamp=self.now)
        self.inbox_path = root / "inbox.json"
        self.inbox = DurableInbox(AtomicJsonStore(self.inbox_path))
        self.server = AircraftLinkServer(
            self.inbox,
            self.gate,
            psk=KEY,
            auth_store=AtomicJsonStore(root / "elf-auth.json"),
            clock=lambda: self.now,
            session_nonce=b"E" * 16,
            challenge=b"C" * 32,
        )
        self.rdk_codec = AuthenticatedDatagramCodec(
            KEY, session_nonce=b"R" * 16
        )
        self.link = AircraftLink(max_attempts=4, retry_interval=1.0)
        self._authenticate()

    def _authenticate(self):
        challenge = self.decode(self.server.challenge_datagram())
        response = Frame(
            MessageType.AUTH_RESPONSE,
            0,
            900,
            uuid.UUID(int=0),
            {"challenge": challenge.payload["challenge"]},
        )
        replies = self.server.feed_bytes(self.seal(encode_frame(response)))
        self.assert_types(replies, MessageType.STATUS)

    def seal(self, raw):
        return self.rdk_codec.seal(raw)

    def decode(self, packet):
        return decode_frame(self.rdk_codec.open(packet))

    def deliver(self, raw, *, corrupt_crc=False):
        if corrupt_crc:
            raw = raw[:-1] + bytes([raw[-1] ^ 0xFF])
        replies = self.server.feed_bytes(self.seal(raw))
        decoded = [self.decode(packet) for packet in replies]
        for response in decoded:
            if response.message_type in (MessageType.ACK, MessageType.NACK):
                self.link.accept_response(response)
        return decoded

    def assert_types(self, packets, *types):
        actual = [self.decode(packet).message_type for packet in packets]
        if actual != list(types):
            raise AssertionError(f"{actual!r} != {list(types)!r}")


class RdkElfFaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.harness = LinkHarness(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_drop_duplicate_reorder_bad_crc_and_delay_still_stage_once(self):
        command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
        command = CloudCommand(
            command_id,
            100.0,
            "aircraft",
            "mission",
            mission_payload(),
        )
        self.harness.link.execute(command, now=0.0)
        first = self.harness.link.due_bytes(0.0)
        decoded = [decode_frame(raw) for raw in first]
        by_type = {}
        items = {}
        for raw, frame in zip(first, decoded):
            if frame.message_type is MessageType.MISSION_ITEM:
                items[frame.payload["index"]] = raw
            else:
                by_type[frame.message_type] = raw

        self.harness.deliver(by_type[MessageType.MISSION_BEGIN])
        self.harness.deliver(items[1])  # deliberately out of order
        self.harness.deliver(items[1])  # duplicate with a fresh auth counter
        self.harness.deliver(
            by_type[MessageType.MISSION_COMMIT], corrupt_crc=True
        )
        # Item 0 is dropped. The sender waits until its retry deadline.
        self.assertEqual(self.harness.link.pending_count, 2)
        self.assertEqual(self.harness.link.due_bytes(0.9), [])

        delayed = self.harness.link.due_bytes(1.0)
        delayed_frames = [decode_frame(raw) for raw in delayed]
        delayed.sort(
            key=lambda raw: (
                decode_frame(raw).message_type is MessageType.MISSION_COMMIT
            )
        )
        for raw in delayed:
            self.harness.deliver(raw)

        self.assertEqual(self.harness.link.pending_count, 0)
        self.assertEqual(
            self.harness.link.transaction_state()["stage"], "acknowledged"
        )
        self.assertGreaterEqual(self.harness.server.metrics["rejected_frame"], 1)
        self.assertEqual(
            [frame.message_type for frame in delayed_frames],
            [MessageType.MISSION_ITEM, MessageType.MISSION_COMMIT],
        )
        self.assertEqual(self.harness.inbox.active["stage"], "MISSION_STAGED")
        self.assertEqual(len(self.harness.inbox.active["items"]), 2)

        restarted = DurableInbox(
            AtomicJsonStore(self.harness.inbox_path)
        )
        self.assertEqual(restarted.active, self.harness.inbox.active)
        self.assertEqual(restarted.queue_depth, 0)

    def test_optical_loss_emits_no_details_and_accepts_no_command(self):
        self.harness.gate.set_blocked(
            "beam_interrupted", timestamp=self.harness.now
        )
        status = self.harness.server.poll_status(
            {"mode": "GUIDED", "lat": 321197000, "secret": "must-not-leak"}
        )
        self.assertEqual(len(status), 1)
        blocked = self.harness.decode(status[0])
        self.assertEqual(blocked.message_type, MessageType.LINK_BLOCKED)
        self.assertEqual(
            set(blocked.payload), {"reason", "timestamp"}
        )

        command = Frame(
            MessageType.COMMAND,
            0,
            901,
            uuid.uuid4(),
            {"action": "arm", "parameters": {}},
        )
        replies = self.harness.server.feed_bytes(
            self.harness.seal(encode_frame(command))
        )
        self.harness.assert_types(replies, MessageType.LINK_BLOCKED)
        self.assertIsNone(self.harness.inbox.active)
        self.assertEqual(self.harness.inbox.queue_depth, 0)


class DemoPreflightTests(unittest.TestCase):
    def run_preflight(self, *args):
        secret = "preflight-must-never-print-this"
        result = subprocess.run(
            ["bash", str(PREFLIGHT), *args],
            cwd=ROOT,
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "RDK_WIFI_PASSWORD": secret,
                "LIOT_PSK": secret,
            },
            text=True,
            capture_output=True,
            check=False,
        )
        return result, secret

    def test_dry_run_is_read_only_complete_and_secret_safe(self):
        result, secret = self.run_preflight("--dry-run")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for marker in (
            "READ-ONLY",
            "RDK/Tuya",
            "L610",
            "192.168.4.1",
            "ELF",
            "optical",
            "durable",
            "NO outdoor or motor tests",
        ):
            self.assertIn(marker, output)
        self.assertNotIn(secret, output)
        for forbidden in (
            "nmcli connection up",
            "systemctl start",
            "systemctl restart",
            "MISSION_COUNT",
            "ARM",
        ):
            self.assertNotIn(forbidden, output)

    def test_role_dry_runs_only_report_checks_owned_by_that_host(self):
        rdk, secret = self.run_preflight("--dry-run", "--role", "rdk")
        elf, _ = self.run_preflight("--dry-run", "--role", "elf")
        self.assertEqual(rdk.returncode, 0, rdk.stderr)
        self.assertIn("RDK/Tuya", rdk.stdout)
        self.assertIn("L610", rdk.stdout)
        self.assertNotIn("ELF services", rdk.stdout)
        self.assertEqual(elf.returncode, 0, elf.stderr)
        self.assertIn("ELF services", elf.stdout)
        self.assertIn("optical", elf.stdout)
        self.assertIn("durable", elf.stdout)
        self.assertNotIn("L610 default route", elf.stdout)
        self.assertNotIn(secret, rdk.stdout + elf.stdout)

    def test_live_software_checks_fail_closed_without_mutating_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            systemctl = bin_dir / "systemctl"
            systemctl.write_text("#!/bin/sh\n[ \"$1\" = is-active ]\n")
            systemctl.chmod(0o755)
            route = bin_dir / "ip"
            route.write_text(
                "#!/bin/sh\n"
                "case \"$3\" in\n"
                "  139.196.6.123) echo '139.196.6.123 dev enxf04bb3b9ebe5' ;;\n"
                "  192.168.4.1) echo '192.168.4.1 dev wlan0' ;;\n"
                "esac\n"
            )
            route.chmod(0o755)
            files = {}
            for name in ("rdk", "health", "inbox", "optical"):
                path = root / f"{name}.json"
                path.write_text("{}")
                files[name] = str(path)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
                    "RDK_STATE_FILE": files["rdk"],
                    "ELF_HEALTH_FILE": files["health"],
                    "ELF_INBOX_FILE": files["inbox"],
                    "OPTICAL_STATE_FILE": files["optical"],
                }
            )
            passing = subprocess.run(
                ["bash", str(PREFLIGHT), "--role", "all"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
            self.assertIn("SUMMARY", passing.stdout)

            route.write_text("#!/bin/sh\necho '139.196.6.123 dev eth0'\n")
            route.chmod(0o755)
            failing = subprocess.run(
                ["bash", str(PREFLIGHT), "--role", "rdk"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(failing.returncode, 0)
            self.assertIn("FAIL L610", failing.stdout)


if __name__ == "__main__":
    unittest.main()
