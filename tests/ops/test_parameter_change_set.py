import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "ops" / "backup_ardupilot_params.py"
APPLY = ROOT / "ops" / "apply_ardupilot_params.py"


class FakeMessage:
    def __init__(self, kind, **fields):
        self.kind = kind
        self.__dict__.update(fields)

    def get_type(self):
        return self.kind


class FakeConnection:
    def __init__(self, messages):
        self.messages = list(messages)
        self.set_calls = []

    def wait_heartbeat(self, timeout):
        return self.messages[0]

    def mav(self):
        return None

    def recv_match(self, type=None, blocking=True, timeout=None):
        del blocking, timeout
        while self.messages:
            message = self.messages.pop(0)
            if type is None or message.get_type() in type:
                return message
        return None


class ParameterSnapshotTests(unittest.TestCase):
    def test_collects_complete_parameter_snapshot_without_writing(self):
        from ops.backup_ardupilot_params import collect_snapshot

        connection = FakeConnection(
            [
                FakeMessage(
                    "HEARTBEAT",
                    autopilot=3,
                    vehicle_type=2,
                    firmware="ArduCopter-4.5.7",
                ),
                FakeMessage(
                    "PARAM_VALUE",
                    param_id="PLND_EST_TYPE",
                    param_value=0.0,
                    param_type=6,
                    param_index=0,
                    param_count=2,
                ),
                FakeMessage(
                    "PARAM_VALUE",
                    param_id="PLND_TYPE",
                    param_value=1.0,
                    param_type=6,
                    param_index=1,
                    param_count=2,
                ),
            ]
        )

        snapshot = collect_snapshot(
            connection,
            vehicle="Copter",
            firmware="ArduCopter-4.5.7",
            timeout=0.1,
        )
        self.assertEqual(snapshot["vehicle"], "Copter")
        self.assertEqual(snapshot["firmware"], "ArduCopter-4.5.7")
        self.assertEqual(set(snapshot["parameters"]), {"PLND_EST_TYPE", "PLND_TYPE"})
        self.assertEqual(snapshot["parameters"]["PLND_EST_TYPE"]["value"], 0.0)
        self.assertEqual(snapshot["parameters"]["PLND_TYPE"]["type"], 6)
        self.assertFalse(snapshot["write_performed"])

    def test_snapshot_rejects_duplicate_or_missing_parameter_identity(self):
        from ops.backup_ardupilot_params import SnapshotError, validate_snapshot

        with self.assertRaises(SnapshotError):
            validate_snapshot(
                {
                    "vehicle": "Copter",
                    "firmware": "ArduCopter-4.5.7",
                    "parameters": {
                        "X": {"value": 1, "type": 6},
                        "x": {"value": 2, "type": 6},
                    },
                }
            )


class ChangeSetTests(unittest.TestCase):
    def test_change_set_requires_audit_fields_and_restore_command(self):
        from ops.apply_ardupilot_params import ChangeSetError, validate_change_set

        valid = {
            "vehicle": "Copter",
            "firmware": "ArduCopter-4.5.7",
            "changes": [
                {
                    "name": "PLND_EST_TYPE",
                    "old": 0,
                    "new": 0,
                    "official_url": "https://ardupilot.org/copter/docs/parameters.html",
                    "reason": "审计记录，无需写入",
                    "restore_command": "param set PLND_EST_TYPE 0",
                }
            ],
        }
        validate_change_set(valid)
        invalid = json.loads(json.dumps(valid))
        del invalid["changes"][0]["official_url"]
        with self.assertRaises(ChangeSetError):
            validate_change_set(invalid)

    def test_vehicle_and_firmware_mismatch_are_rejected(self):
        from ops.apply_ardupilot_params import ChangeSetError, validate_change_set

        changes = {
            "vehicle": "Copter",
            "firmware": "ArduCopter-4.5.7",
            "changes": [],
        }
        with self.assertRaisesRegex(ChangeSetError, "vehicle"):
            validate_change_set(changes, vehicle="Rover")
        with self.assertRaisesRegex(ChangeSetError, "firmware"):
            validate_change_set(changes, firmware="ArduRover-4.5.7")

    def test_apply_never_writes_parameters_even_when_execute_is_requested(self):
        from ops.apply_ardupilot_params import apply_changes

        class NoWriteConnection:
            def __init__(self):
                self.calls = []

            def param_set_send(self, *args):
                self.calls.append(args)

        connection = NoWriteConnection()
        result = apply_changes(
            connection,
            {
                "vehicle": "Copter",
                "firmware": "ArduCopter-4.5.7",
                "changes": [
                    {
                        "name": "PLND_EST_TYPE",
                        "old": 0,
                        "new": 1,
                        "official_url": "https://ardupilot.org/copter/docs/parameters.html",
                        "reason": "test",
                        "restore_command": "param set PLND_EST_TYPE 0",
                    }
                ],
            },
            vehicle="Copter",
            firmware="ArduCopter-4.5.7",
            execute=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(connection.calls, [])

    def test_cli_defaults_to_dry_run_and_does_not_need_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            changes = Path(tmp) / "changes.json"
            changes.write_text(
                json.dumps(
                    {
                        "vehicle": "Copter",
                        "firmware": "ArduCopter-4.5.7",
                        "changes": [],
                    }
                )
            )
            result = subprocess.run(
                [sys.executable, str(APPLY), "--changes", str(changes)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertNotIn("param_set", result.stdout)

    def test_docs_record_no_required_project_parameter_write(self):
        doc = (ROOT / "docs/operations/ardupilot-parameters.md").read_text()
        self.assertIn("没有必要参数写入", doc)
        self.assertIn("PLND_EST_TYPE", doc)
        self.assertIn("恢复命令", doc)


if __name__ == "__main__":
    unittest.main()
