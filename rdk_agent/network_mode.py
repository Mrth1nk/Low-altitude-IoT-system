"""Restricted cloud-to-root request boundary for RDK Wi-Fi mode changes."""

import os
from pathlib import Path

try:
    from .command_router import CommandRejected
except ImportError:
    from command_router import CommandRejected


class NetworkModeExecutor:
    ALLOWED_MODES = frozenset(("phone", "aircraft"))

    def __init__(self, request_path):
        self.request_path = Path(request_path)

    def execute(self, command):
        if command.target != "system" or command.action != "network_mode":
            raise CommandRejected("unsupported system action")
        mode = str(command.payload.get("mode", "")).strip().lower()
        if mode not in self.ALLOWED_MODES:
            raise CommandRejected("unsupported network mode")

        self.request_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.request_path.with_name(
            f".{self.request_path.name}.{os.getpid()}.tmp"
        )
        temporary.write_text(mode + "\n", encoding="ascii")
        os.replace(temporary, self.request_path)
        return {
            "accepted": True,
            "stage": "scheduled",
            "message": f"network switch to {mode} scheduled",
        }
