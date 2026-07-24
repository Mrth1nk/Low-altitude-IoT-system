"""Retry and receive-side state for reliable LIOT delivery."""

from collections import OrderedDict, deque
from dataclasses import dataclass, field

from .frame import Frame, MessageType
from .messages import validate_payload


class RetryExhausted(RuntimeError):
    def __init__(self, command_id, sequence):
        self.command_id = command_id
        self.sequence = sequence
        super().__init__(
            f"retry attempts exhausted for command {command_id}, sequence {sequence}"
        )


@dataclass
class _Pending:
    frame: Frame
    attempts: int
    next_due: float


class RetrySender:
    def __init__(self, max_attempts=3, retry_interval=1.0, max_pending=1024):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_interval <= 0:
            raise ValueError("retry_interval must be positive")
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        self.max_attempts = max_attempts
        self.retry_interval = retry_interval
        self.max_pending = max_pending
        self._pending = {}
        self._exhausted = deque(maxlen=max_pending)

    @property
    def pending_count(self):
        return len(self._pending)

    def queue(self, frame, now):
        if not isinstance(frame, Frame):
            raise TypeError("frame must be a Frame")
        identity = (frame.command_id, frame.sequence)
        if identity in self._pending:
            raise ValueError("command and sequence are already pending")
        if len(self._pending) >= self.max_pending:
            raise ValueError("pending retry limit reached")
        self._pending[identity] = _Pending(frame, 0, float(now))
        return True

    def acknowledge(self, command_id, sequence):
        return self._pending.pop((command_id, sequence), None) is not None

    def pop_exhausted(self):
        exhausted = list(self._exhausted)
        self._exhausted.clear()
        return exhausted

    def due(self, now):
        now = float(now)
        due_frames = []
        for identity, pending in list(self._pending.items()):
            if now < pending.next_due:
                continue
            if pending.attempts >= self.max_attempts:
                del self._pending[identity]
                self._exhausted.append(identity)
                continue
            pending.attempts += 1
            pending.next_due = now + self.retry_interval
            due_frames.append(pending.frame)
        return due_frames


@dataclass
class MissionReceiveState:
    command_id: object
    mission_id: str
    item_count: int
    vehicle: str
    updated_at: float
    items: dict = field(default_factory=dict)
    committed: bool = False

    @property
    def received_indices(self):
        return tuple(sorted(self.items))

    @property
    def missing_indices(self):
        return tuple(index for index in range(self.item_count) if index not in self.items)


class ReceiverState:
    def __init__(
        self,
        history_limit=1024,
        max_missions=8,
        mission_ttl=300.0,
        max_missing_page=32,
    ):
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        if max_missions < 1:
            raise ValueError("max_missions must be positive")
        if mission_ttl <= 0:
            raise ValueError("mission_ttl must be positive")
        if max_missing_page < 1:
            raise ValueError("max_missing_page must be positive")
        self.history_limit = history_limit
        self.max_missions = max_missions
        self.mission_ttl = float(mission_ttl)
        self.max_missing_page = max_missing_page
        self._seen = OrderedDict()
        self._missions = {}
        self._last_now = None

    def mission(self, command_id, mission_id, now=0.0):
        now = self._prepare_now(now)
        self._expire(now)
        return self._missions.get((command_id, mission_id))

    def resume(self, command_id, mission_id, offset=0, limit=None, now=0.0):
        now = self._prepare_now(now)
        self._expire(now)
        mission = self._require_mission(command_id, mission_id)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if limit is None:
            limit = self.max_missing_page
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(limit, self.max_missing_page)
        remaining = [index for index in mission.missing_indices if index >= offset]
        page = remaining[:limit]
        next_offset = page[-1] + 1 if len(remaining) > len(page) else None
        return {"missing_indices": page, "next_offset": next_offset}

    def accept(self, frame, now=0.0):
        if not isinstance(frame, Frame):
            raise TypeError("frame must be a Frame")
        now = self._prepare_now(now)
        self._expire(now)
        validate_payload(frame.message_type, frame.payload)
        dedup_key = (frame.command_id, frame.sequence)
        if dedup_key in self._seen:
            return False

        if frame.message_type is MessageType.MISSION_BEGIN:
            self._begin(frame.command_id, frame.payload, now)
        elif frame.message_type is MessageType.MISSION_ITEM:
            self._item(frame.command_id, frame.payload, now)
        elif frame.message_type is MessageType.MISSION_COMMIT:
            self._commit(frame.command_id, frame.payload, now)

        self._seen[dedup_key] = None
        self._seen.move_to_end(dedup_key)
        while len(self._seen) > self.history_limit:
            self._seen.popitem(last=False)
        return True

    def _prepare_now(self, now):
        now = float(now)
        if self._last_now is not None and now < self._last_now:
            raise ValueError("now must be monotonic")
        self._last_now = now
        return now

    def _expire(self, now):
        expired = [
            identity
            for identity, mission in self._missions.items()
            if now - mission.updated_at >= self.mission_ttl
        ]
        for identity in expired:
            del self._missions[identity]

    def _require_mission(self, command_id, mission_id):
        mission = self._missions.get((command_id, mission_id))
        if mission is None:
            raise ValueError("mission transaction has not begun")
        return mission

    def _begin(self, command_id, payload, now):
        identity = (command_id, payload["mission_id"])
        existing = self._missions.get(identity)
        if existing is not None and (
            existing.item_count != payload["item_count"]
            or existing.vehicle != payload["vehicle"]
        ):
            raise ValueError("mission begin conflicts with existing mission")
        if existing is not None:
            existing.updated_at = now
        else:
            if len(self._missions) >= self.max_missions:
                raise ValueError("concurrent mission limit reached")
            self._missions[identity] = MissionReceiveState(
                command_id,
                payload["mission_id"],
                payload["item_count"],
                payload["vehicle"],
                now,
            )

    def _item(self, command_id, payload, now):
        mission = self._require_mission(command_id, payload["mission_id"])
        index = payload["index"]
        if index >= mission.item_count:
            raise ValueError("mission item index is outside declared range")
        existing = mission.items.get(index)
        if existing is not None and existing != payload:
            raise ValueError("mission item conflicts with received item")
        mission.items[index] = dict(payload)
        mission.updated_at = now

    def _commit(self, command_id, payload, now):
        mission = self._require_mission(command_id, payload["mission_id"])
        if payload["item_count"] != mission.item_count:
            raise ValueError("mission commit count does not match begin")
        if mission.missing_indices:
            raise ValueError("mission is incomplete")
        mission.committed = True
        mission.updated_at = now
