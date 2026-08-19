import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "ops" / "install_slave.sh"
HEALTH = ROOT / "ops" / "health_slave.sh"
UNIT = ROOT / "ops" / "systemd" / "low-altitude-slave.service"


def run_script(path, *args):
    return subprocess.run(
        ["bash", str(path), *args],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )


class SlaveInstallTests(unittest.TestCase):
    def test_unit_uses_fixed_by_id_and_single_unprivileged_owner(self):
        unit = UNIT.read_text()
        self.assertIn("User=sunrise", unit)
        self.assertIn("SupplementaryGroups=dialout", unit)
        self.assertIn("EnvironmentFile=/etc/low-altitude-iot/slave.env", unit)
        self.assertIn("ExecStartPre=/usr/local/lib/low-altitude-iot/health_slave.sh --preflight", unit)
        self.assertIn("ExecStart=/usr/bin/python3 -m slave_agent.main", unit)
        self.assertNotIn("ttyACM0", unit)
        self.assertNotIn("ttyUSB0", unit)

    def test_health_dry_run_checks_identity_heartbeat_peer_and_occupancy(self):
        result = run_script(HEALTH, "--dry-run")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for marker in ("by-id", "Copter heartbeat", "serial owner", "192.168.4.2", "14620"):
            self.assertIn(marker, output)

    def test_installer_is_atomic_reversible_and_does_not_reboot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_script(INSTALL, "--dry-run", "--root", tmp)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for marker in ("PREFLIGHT", "BACKUP", "ATOMIC INSTALL", "HEALTH", "ROLLBACK"):
            self.assertIn(marker, output)
        source = INSTALL.read_text()
        self.assertIn("trap rollback", source)
        self.assertIn("mktemp", source)
        self.assertIn("mv -f", source)
        self.assertIn("systemctl stop low-altitude-slave.service", source)
        self.assertIn("seq 1 15", source)
        self.assertNotIn("reboot", source)

    def test_installer_requires_root_owned_mode_0600_environment(self):
        source = INSTALL.read_text()
        self.assertIn("/etc/low-altitude-iot/slave.env", source)
        self.assertIn("stat -c %u", source)
        self.assertIn("stat -c %a", source)
        self.assertIn("SLAVE_FC_DEVICE=/dev/serial/by-id/", source)


if __name__ == "__main__":
    unittest.main()
