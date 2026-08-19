import json
from pathlib import Path
import tempfile
import unittest

from ops.configure_follow import (
    FOLLOW_VALUES,
    FollowConfigurationError,
    apply_follow_values,
    collect_follow_snapshot,
    persist_recovery_bundle,
)


class Message:
    def __init__(self, kind, **fields):
        self.kind = kind
        self.__dict__.update(fields)

    def get_type(self):
        return self.kind


class Mav:
    def __init__(self, connection):
        self.connection = connection
        self.set_calls = []

    def param_request_read_send(self, _system, _component, name, _index):
        self.connection.requested = name.decode().rstrip("\x00")

    def param_set_send(self, _system, _component, name, value, param_type):
        name = name.decode().rstrip("\x00")
        self.set_calls.append((name, float(value), int(param_type)))
        self.connection.values[name] = float(value)
        self.connection.requested = name


class Connection:
    def __init__(self, values=None, *, vehicle_type=2, armed=False):
        self.values = dict(values or {name: 0.0 for name in FOLLOW_VALUES})
        self.requested = None
        self.target_system = 2
        self.target_component = 1
        self.heartbeat = Message(
            "HEARTBEAT",
            type=vehicle_type,
            base_mode=128 if armed else 0,
            autopilot=3,
        )
        self.mav = Mav(self)

    def wait_heartbeat(self, timeout):
        return self.heartbeat

    def recv_match(self, type=None, blocking=True, timeout=None):
        del type, blocking, timeout
        if self.requested not in self.values:
            return None
        name = self.requested
        self.requested = None
        return Message(
            "PARAM_VALUE",
            param_id=name,
            param_value=self.values[name],
            param_type=9,
        )


class FollowConfigurationTests(unittest.TestCase):
    def test_slave_installer_deploys_recovery_tool(self):
        installer = (Path(__file__).resolve().parents[2] / "ops" / "install_slave.sh").read_text()
        self.assertIn("configure_follow.py", installer)
        self.assertIn("/usr/local/lib/low-altitude-iot/configure_follow.py", installer)

    def test_snapshot_contains_every_modified_parameter_without_writing(self):
        connection = Connection({name: float(index) for index, name in enumerate(FOLLOW_VALUES)})

        snapshot = collect_follow_snapshot(
            connection,
            device="/dev/serial/by-id/usb-ArduPilot-test-if00",
            timeout=0.1,
        )

        self.assertEqual(set(snapshot["parameters"]), set(FOLLOW_VALUES))
        self.assertEqual(connection.mav.set_calls, [])
        self.assertFalse(snapshot["write_performed"])

    def test_recovery_bundle_is_persisted_before_apply_and_restorable(self):
        original = {name: float(index + 10) for index, name in enumerate(FOLLOW_VALUES)}
        connection = Connection(original)
        snapshot = collect_follow_snapshot(
            connection,
            device="/dev/serial/by-id/usb-ArduPilot-test-if00",
            timeout=0.1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path, restore_path = persist_recovery_bundle(snapshot, Path(tmp))
            result = apply_follow_values(connection, snapshot, timeout=0.1)

            self.assertTrue(snapshot_path.exists())
            self.assertEqual(json.loads(snapshot_path.read_text())["parameters"], snapshot["parameters"])
            self.assertIn("param_set_send", restore_path.read_text())
            self.assertTrue(result["verified"])
            self.assertEqual(
                {name: connection.values[name] for name in FOLLOW_VALUES},
                {name: float(value) for name, value in FOLLOW_VALUES.items()},
            )

    def test_rejects_armed_non_copter_wrong_device_and_missing_parameter(self):
        cases = [
            (Connection(armed=True), "/dev/serial/by-id/usb-ArduPilot-test-if00", "disarmed"),
            (Connection(vehicle_type=10), "/dev/serial/by-id/usb-ArduPilot-test-if00", "Copter"),
            (Connection(), "/dev/ttyACM0", "by-id"),
            (Connection({"SYSID_THISMAV": 1}), "/dev/serial/by-id/usb-ArduPilot-test-if00", "missing"),
        ]
        for connection, device, expected in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(
                FollowConfigurationError, expected
            ):
                collect_follow_snapshot(connection, device=device, timeout=0.01)


if __name__ == "__main__":
    unittest.main()
