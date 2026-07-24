import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONFIGURE = ROOT / "ops" / "configure_rdk_network.sh"
HEALTH = ROOT / "ops" / "health_rdk.sh"
INSTALL = ROOT / "ops" / "install_rdk.sh"
SYSTEMD = ROOT / "ops" / "systemd"
SECRET = "never-print-this-password"


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


class ConfigureRdkNetworkTests(unittest.TestCase):
    def test_demo_dry_run_uses_isolated_wifi_route_without_leaking_secret(self):
        result = run_script(
            CONFIGURE,
            "--dry-run",
            "--mode",
            "demo",
            extra_env={"RDK_WIFI_PASSWORD": SECRET},
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("woshinailong", output)
        self.assertIn("ipv4.never-default yes", output)
        self.assertIn("192.168.4.1/32", output)
        self.assertIn("wlan0", output)
        self.assertNotIn(SECRET, output)
        self.assertIn("<redacted>", output)

    def test_development_mode_does_not_activate_demo_profile(self):
        result = run_script(CONFIGURE, "--dry-run", "--mode", "development")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("autoconnect no", output)
        self.assertNotIn("connection up low-altitude-aircraft", output)

    def test_demo_persistence_is_explicit(self):
        transient = run_script(
            CONFIGURE,
            "--dry-run",
            "--mode",
            "demo",
            extra_env={"RDK_WIFI_PASSWORD": SECRET},
        )
        persistent = run_script(
            CONFIGURE,
            "--dry-run",
            "--mode",
            "demo",
            "--persist",
            extra_env={"RDK_WIFI_PASSWORD": SECRET},
        )
        self.assertIn("connection.autoconnect no", transient.stdout)
        self.assertIn("connection.autoconnect yes", persistent.stdout)

        development = run_script(
            CONFIGURE, "--dry-run", "--mode", "development", "--persist"
        )
        self.assertIn("connection.autoconnect no", development.stdout)

    def test_recovery_only_changes_wifi_and_never_mentions_secret(self):
        result = run_script(
            CONFIGURE,
            "--dry-run",
            "--mode",
            "recover",
            "--development-profile",
            "Mr.think的Mate 70 Pro+",
            extra_env={"RDK_WIFI_PASSWORD": SECRET},
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("connection down low-altitude-aircraft", output)
        self.assertIn("connection up Mr.think的Mate 70 Pro+", output)
        self.assertNotIn("enxf04bb3b9ebe5", output)
        self.assertNotIn("ip route", output)
        self.assertNotIn(SECRET, output)

    def test_password_file_policy_is_root_only_and_shell_never_traces(self):
        source = CONFIGURE.read_text()
        self.assertIn("stat -c %u", source)
        self.assertIn("stat -c %a", source)
        self.assertIn("RDK_WIFI_PASSWORD_FILE", source)
        self.assertNotIn("set -x", source)


class HealthAndServiceTests(unittest.TestCase):
    def test_health_dry_run_checks_cloud_and_aircraft_routes(self):
        result = run_script(
            HEALTH,
            "--dry-run",
            extra_env={"L610_INTERFACE": "enxf04bb3b9ebe5"},
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("enxf04bb3b9ebe5", output)
        self.assertIn("192.168.4.1", output)
        self.assertIn("wlan0", output)
        self.assertIn("aircraft link is optional", output.lower())

    def test_base_service_does_not_require_aircraft_network(self):
        base = (SYSTEMD / "low-altitude-rdk.service").read_text()
        network = (SYSTEMD / "low-altitude-rdk-aircraft-network.service").read_text()
        for coupling in ("Requires=", "BindsTo=", "PartOf="):
            self.assertNotIn(coupling + "low-altitude-rdk-aircraft-network", base)
        self.assertNotIn("After=low-altitude-rdk-aircraft-network", base)
        self.assertIn("configure_rdk_network.sh --mode demo --persist", network)

    def test_base_service_loads_root_owned_runtime_environment(self):
        base = (SYSTEMD / "low-altitude-rdk.service").read_text()
        self.assertIn(
            "EnvironmentFile=/etc/low-altitude-iot/rdk.env",
            base,
        )


class InstallerTests(unittest.TestCase):
    def test_installer_dry_run_has_transaction_and_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_script(
                INSTALL,
                "--dry-run",
                "--root",
                tmp,
                "--enable-demo-network",
                extra_env={"RDK_WIFI_PASSWORD": SECRET},
            )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        for marker in ("PREFLIGHT", "BACKUP", "ATOMIC INSTALL", "HEALTH", "ROLLBACK"):
            self.assertIn(marker, output)
        self.assertIn("low-altitude-rdk.service", output)
        self.assertIn("low-altitude-rdk-aircraft-network.service", output)
        self.assertNotIn(SECRET, output)

    def test_installer_defaults_to_ssh_friendly_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_script(INSTALL, "--dry-run", "--root", tmp)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("disable low-altitude-rdk-aircraft-network.service", output)
        self.assertNotIn("enable --now low-altitude-rdk-aircraft-network.service", output)

    def test_installer_contains_health_rollback_trap(self):
        source = INSTALL.read_text()
        self.assertIn("set -Eeuo pipefail", source)
        self.assertIn("rollback", source)
        self.assertIn("trap", source)
        self.assertIn("health_rdk.sh", source)
        self.assertIn("mktemp", source)
        self.assertIn("mv -f", source)
        self.assertIn("BASE_WAS_ENABLED", source)
        self.assertIn("AIRCRAFT_WAS_ENABLED", source)
        self.assertIn("restore_service_state", source)

    def test_rollback_never_copies_backup_root_metadata_onto_root(self):
        source = INSTALL.read_text()
        self.assertNotIn('cp -a "$BACKUP_DIR/." "$(dest /)/"', source)
        self.assertIn('restore_backup_tree', source)

    def test_installer_requires_root_only_aircraft_link_psk(self):
        source = INSTALL.read_text()
        self.assertIn("/etc/low-altitude-iot/rdk.env", source)
        self.assertIn("AIRCRAFT_LINK_PSK", source)
        self.assertIn("stat -c %u", source)
        self.assertIn("stat -c %a", source)

    def test_optional_aircraft_start_failure_is_nonfatal(self):
        source = INSTALL.read_text()
        self.assertIn("aircraft network failed; Rover/Tuya remain active", source)
        self.assertNotIn(
            "systemctl enable --now low-altitude-rdk-aircraft-network.service",
            source,
        )


class StartRoverStackTests(unittest.TestCase):
    def test_start_script_supports_external_existing_virtualenv(self):
        source = (ROOT / "rdk_agent" / "start_rover_stack.sh").read_text()
        self.assertIn("RDK_VENV", source)
        self.assertIn('/home/sunrise/uav_tuya_agent/.venv', source)
        self.assertIn('"$RDK_VENV/bin/activate"', source)


if __name__ == "__main__":
    unittest.main()
