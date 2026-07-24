import tempfile
import unittest
import uuid
from pathlib import Path

from rdk_agent.command_router import CloudCommand, CommandRejected
from rdk_agent.network_mode import NetworkModeExecutor


class NetworkModeExecutorTests(unittest.TestCase):
    def command(self, mode):
        return CloudCommand(
            uuid.uuid4(),
            1_800_000_000.0,
            "system",
            "network_mode",
            {"mode": mode},
        )

    def test_writes_only_whitelisted_mode_to_root_watched_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / "network-mode"
            executor = NetworkModeExecutor(request)

            result = executor.execute(self.command("aircraft"))

            self.assertEqual(request.read_text(), "aircraft\n")
            self.assertEqual(result["stage"], "scheduled")
            self.assertTrue(result["accepted"])

    def test_rejects_unknown_system_action_and_network_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = NetworkModeExecutor(Path(tmp) / "network-mode")
            bad_action = CloudCommand(
                uuid.uuid4(),
                1_800_000_000.0,
                "system",
                "shell",
                {"mode": "phone"},
            )

            with self.assertRaisesRegex(CommandRejected, "system action"):
                executor.execute(bad_action)
            with self.assertRaisesRegex(CommandRejected, "network mode"):
                executor.execute(self.command("other"))


if __name__ == "__main__":
    unittest.main()
