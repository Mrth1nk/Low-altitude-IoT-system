import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"
SYSTEMD = OPS / "systemd"
INSTALL = OPS / "install_aircraft.sh"
CHECK_ROLES = OPS / "check_serial_roles.sh"
HEALTH = OPS / "health_aircraft.sh"
SECRET = "task-nine-secret-must-not-leak"


def run_script(path, *args, extra_env=None):
    env = os.environ.copy()
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(path), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class AircraftServiceTests(unittest.TestCase):
    def test_services_have_disjoint_device_ownership_and_restart_limits(self):
        aircraft = (SYSTEMD / "low-altitude-aircraft.service").read_text()
        vision = (SYSTEMD / "low-altitude-vision.service").read_text()

        self.assertIn("check_serial_roles.sh aircraft", aircraft)
        self.assertIn("DeviceAllow=/dev/ttyACM0 rw", aircraft)
        self.assertIn("DeviceAllow=/dev/ttyUSB0 rw", aircraft)
        self.assertNotIn("/dev/ttyS9", aircraft)
        self.assertNotIn("/dev/video", aircraft)

        self.assertIn("check_serial_roles.sh vision", vision)
        self.assertIn("DeviceAllow=/dev/ttyS9 rw", vision)
        self.assertIn("DeviceAllow=char-video4linux rw", vision)
        self.assertNotIn("/dev/ttyACM0", vision)
        self.assertNotIn("/dev/ttyUSB0", vision)

        for unit in (aircraft, vision):
            self.assertIn("Restart=on-failure", unit)
            self.assertIn("StartLimitIntervalSec=", unit)
            self.assertIn("StartLimitBurst=", unit)
            self.assertIn(
                "EnvironmentFile=/etc/low-altitude-iot/aircraft.env", unit
            )

    def test_role_check_dry_run_covers_identity_ambiguity_and_occupancy(self):
        result = run_script(CHECK_ROLES, "--dry-run", "all")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for expected in (
            "/dev/ttyACM0",
            "/dev/ttyUSB0",
            "/dev/ttyS9",
            "/dev/video0",
            "by-id",
            "udevadm",
            "ambiguous",
            "lsof",
            "fuser",
        ):
            self.assertIn(expected, output)

        source = CHECK_ROLES.read_text()
        self.assertIn("readlink -f", source)
        self.assertIn("udevadm info", source)
        self.assertIn("compgen -G", source)
        self.assertIn("device is busy", source)


class AircraftRuntimeStateTests(unittest.TestCase):
    def test_optical_state_is_atomic_stale_safe_and_gates_execution(self):
        from aircraft_agent.main import (
            GatedMavlinkSession,
            GatedOperations,
            OpticalStateReader,
        )
        from aircraft_agent.optical_gate import OpticalBlocked
        from aircraft_agent.state_store import AtomicJsonStore
        from vision.runtime import OpticalStatePublisher

        class Operations:
            def __init__(self):
                self.calls = []

            def arm(self, value):
                self.calls.append(("arm", value))

            def set_mode(self, mode):
                self.calls.append(("mode", mode))

            def execute(self, record, start_auto=False):
                self.calls.append(("mission", record, start_auto))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "optical.json"
            publisher = OpticalStatePublisher(AtomicJsonStore(path))
            now = time.time()
            publisher.publish(
                locked=False,
                confidence=0.0,
                area=0.0,
                mode="LAND",
                timestamp=now,
                last_error="target_lost",
            )
            reader = OpticalStateReader(path, max_age_s=0.5, clock=lambda: now)
            operations = Operations()
            gated = GatedOperations(operations, reader)
            with self.assertRaises(OpticalBlocked):
                gated.arm(True)

            publisher.publish(
                locked=True,
                confidence=0.9,
                area=100.0,
                mode="GUIDED",
                timestamp=now,
                last_error="",
            )
            gated.set_mode("GUIDED")
            self.assertEqual(operations.calls, [("mode", "GUIDED")])

            class Session:
                def __init__(self):
                    self.sent = []

                def send(self, kind, **fields):
                    self.sent.append((kind, fields))

            session = Session()
            guarded_session = GatedMavlinkSession(session, reader)
            guarded_session.send("MISSION_COUNT", count=2)
            publisher.publish(
                locked=False,
                confidence=0.0,
                area=0.0,
                mode="LAND",
                timestamp=now,
                last_error="beam_interrupted",
            )
            with self.assertRaises(OpticalBlocked):
                guarded_session.send("MISSION_ITEM_INT", item={"seq": 0})
            self.assertEqual(len(session.sent), 1)

            stale = OpticalStateReader(
                path, max_age_s=0.5, clock=lambda: now + 1.0
            )
            with self.assertRaises(OpticalBlocked):
                GatedOperations(operations, stale).execute({"kind": "mission"})

    def test_health_snapshot_and_transaction_log_are_durable(self):
        from aircraft_agent.main import HealthSnapshotWriter, TransactionJournal
        from aircraft_agent.state_store import AtomicJsonStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health_path = root / "health.json"
            writer = HealthSnapshotWriter(AtomicJsonStore(health_path))
            writer.write(
                command_id="command-1",
                queue_depth=2,
                fc_heartbeat_at=123.5,
                optical={"locked": True, "blocked": False},
                last_error="",
            )
            snapshot = json.loads(health_path.read_text())
            self.assertEqual(
                set(snapshot),
                {
                    "timestamp",
                    "command_id",
                    "queue_depth",
                    "fc_heartbeat_at",
                    "optical",
                    "last_error",
                },
            )

            log_path = root / "transactions.jsonl"
            first = TransactionJournal(log_path, max_bytes=256, backups=2)
            self.assertTrue(
                first.append(
                    {"command_id": "command-1", "stage": "COMPLETED"}
                )
            )
            restarted = TransactionJournal(log_path, max_bytes=256, backups=2)
            self.assertFalse(
                restarted.append(
                    {"command_id": "command-1", "stage": "COMPLETED"}
                )
            )
            self.assertTrue(
                restarted.append(
                    {"command_id": "command-2", "stage": "FAILED"}
                )
            )
            lines = log_path.read_text().splitlines()
            self.assertEqual(len(lines), 2)

    def test_repeated_optical_snapshots_do_not_reset_status_rate_limit(self):
        from aircraft_agent.main import _apply_gate
        from aircraft_agent.optical_gate import OpticalGate

        now = 100.0
        gate = OpticalGate(clock=lambda: now, status_interval=1.0)
        state = {"locked": True, "blocked": False, "timestamp": now}
        _apply_gate(gate, state)
        self.assertIsNotNone(gate.due_frame({}, now=now))
        _apply_gate(gate, {**state, "timestamp": now + 0.1})
        self.assertIsNone(gate.due_frame({}, now=now + 0.1))


class AircraftInstallerTests(unittest.TestCase):
    def test_installer_dry_run_is_transactional_and_secret_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_script(
                INSTALL,
                "--dry-run",
                "--root",
                tmp,
                extra_env={"AIRCRAFT_LINK_PSK": SECRET},
            )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for marker in (
            "PREFLIGHT",
            "BACKUP",
            "ATOMIC INSTALL",
            "HEALTH",
            "ROLLBACK",
            "light-wifi-bridge",
            "light-ir-precision-land",
            "low-altitude-aircraft.service",
            "low-altitude-vision.service",
            "logrotate",
        ):
            self.assertIn(marker, output)
        self.assertNotIn(SECRET, output)
        self.assertIn("<redacted>", output)

    def test_installer_enforces_root_only_env_and_restores_legacy_state(self):
        source = INSTALL.read_text()
        self.assertIn("stat -c %u", source)
        self.assertIn("stat -c %a", source)
        self.assertIn("AIRCRAFT_LINK_PSK", source)
        self.assertNotIn("set -x", source)
        self.assertIn("legacy_state", source)
        self.assertIn("restore_legacy_services", source)
        self.assertIn("health_aircraft.sh", source)
        self.assertIn("source \"$env_file\"", source)
        self.assertIn("mktemp", source)
        self.assertIn("mv -f", source)

    def test_health_dry_run_checks_atomic_snapshot_and_process_roles(self):
        result = run_script(HEALTH, "--dry-run")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for expected in (
            "low-altitude-aircraft.service",
            "low-altitude-vision.service",
            "command_id",
            "queue_depth",
            "fc_heartbeat_at",
            "optical",
            "last_error",
            "/proc",
            "atomic",
        ):
            self.assertIn(expected, output)


if __name__ == "__main__":
    unittest.main()
