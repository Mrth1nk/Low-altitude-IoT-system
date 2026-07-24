from __future__ import annotations

import json
import time
import uuid

from shared_protocol.frame import Frame, MessageType


class OpticalBlocked(RuntimeError):
    pass


class OpticalGate:
    CLOUD_COMMAND_TYPES = frozenset(
        (
            MessageType.COMMAND,
            MessageType.MISSION_BEGIN,
            MessageType.MISSION_ITEM,
            MessageType.MISSION_COMMIT,
        )
    )

    def __init__(self, *, clock=time.time, status_interval=1.0):
        if status_interval <= 0:
            raise ValueError("status_interval must be positive")
        self.clock = clock
        self.status_interval = float(status_interval)
        self._locked = False
        self._reason = "optical_not_locked"
        self._event_timestamp = float(self.clock())
        self._next_status = float("-inf")
        self._sequence = 0

    @property
    def locked(self):
        return self._locked

    def set_locked(self, *, timestamp=None):
        self._locked = True
        self._reason = ""
        self._event_timestamp = self._time(timestamp)
        self._next_status = float("-inf")

    def set_blocked(self, reason="optical_blocked", *, timestamp=None):
        self._locked = False
        self._reason = str(reason)[:128] or "optical_blocked"
        self._event_timestamp = self._time(timestamp)
        self._next_status = float("-inf")

    def require_locked(self, frame):
        if (
            frame.message_type in self.CLOUD_COMMAND_TYPES
            and not self._locked
        ):
            raise OpticalBlocked(self._reason)
        return True

    def due_frame(self, snapshot=None, *, now=None):
        now = self._time(now)
        if now < self._next_status:
            return None
        self._next_status = now + self.status_interval
        if not self._locked:
            return self._frame(
                MessageType.LINK_BLOCKED,
                {
                    "reason": self._reason,
                    "timestamp": self._event_timestamp,
                },
            )
        detail = json.dumps(
            snapshot or {},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        if len(detail) > 256:
            detail = detail[:256]
        return self._frame(
            MessageType.STATUS,
            {"state": "locked", "detail": detail, "timestamp": now},
        )

    def blocked_frame(self):
        if self._locked:
            raise RuntimeError("optical link is locked")
        return self._frame(
            MessageType.LINK_BLOCKED,
            {
                "reason": self._reason,
                "timestamp": self._event_timestamp,
            },
        )

    def _frame(self, message_type, payload):
        frame = Frame(
            message_type,
            0,
            self._sequence,
            uuid.UUID(int=0),
            payload,
        )
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        return frame

    def _time(self, value):
        return float(self.clock() if value is None else value)
