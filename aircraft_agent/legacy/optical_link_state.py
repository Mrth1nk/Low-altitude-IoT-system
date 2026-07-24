"""Shared optical-link state for the IR tracker and telemetry bridge."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_STATUS_FILE = "/tmp/optical_link_status.json"


@dataclass
class OpticalLinkState:
    blocked: bool = True
    state: str = "UNKNOWN"
    age_sec: float | None = None
    stale: bool = False
    reason: str = ""


class OpticalLinkStateReader:
    def __init__(self, path: str | None = None, stale_after_sec: float = 2.0) -> None:
        self.path = Path(path or os.environ.get("OPTICAL_LINK_STATUS_FILE", DEFAULT_STATUS_FILE))
        self.stale_after_sec = float(stale_after_sec)

    def read(self) -> OpticalLinkState:
        try:
            payload: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return OpticalLinkState(blocked=True, reason="missing")
        except Exception as exc:
            return OpticalLinkState(blocked=True, reason=f"invalid:{exc}")

        timestamp = float(payload.get("timestamp", 0.0) or 0.0)
        age = time.time() - timestamp if timestamp else None
        stale = bool(age is not None and age > self.stale_after_sec)
        if stale:
            return OpticalLinkState(
                blocked=True,
                state=str(payload.get("state", "UNKNOWN")),
                age_sec=age,
                stale=True,
                reason="stale",
            )

        return OpticalLinkState(
            blocked=bool(payload.get("link_blocked", False)),
            state=str(payload.get("state", "UNKNOWN")),
            age_sec=age,
            stale=False,
            reason=str(payload.get("reason", "")),
        )


def write_optical_link_state(path: str | None, *, blocked: bool, state: str, detected: bool, lost_frames: int) -> None:
    status_path = Path(path or os.environ.get("OPTICAL_LINK_STATUS_FILE", DEFAULT_STATUS_FILE))
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_name(f"{status_path.name}.{os.getpid()}.tmp")
    payload = {
        "timestamp": time.time(),
        "link_blocked": bool(blocked),
        "state": state,
        "detected": bool(detected),
        "lost_frames": int(lost_frames),
    }
    tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(status_path)
