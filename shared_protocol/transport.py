"""Retry and receive-side state for reliable LIOT delivery."""

from collections import OrderedDict
from dataclasses import dataclass, field

from .frame import Frame, MessageType
from .messages import validate_payload


class RetryExhausted(RuntimeError):
    def __init__(self, sequence):
        self.sequence = sequence
        super().__init__(f"retry attempts exhausted for sequence {sequence}")


@dataclass
class _Pending:
    frame: Frame
    attempts: int
    next_due: float


class RetrySender:
    def __init__(self, max_attempts=3, retry_interval=1.0):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_interval <= 0:
            raise ValueError("retry_interval must be positive")
        self.max_attempts = max_attempts
        self.retry_interval = retry_interval
        self._pending = {}

    @property
    def pending_count(self):
        return len(self._pending)

    def queue(self, frame, now):
        if not isinstance(frame, Frame):
            raise TypeError("frame must be a Frame")
        if frame.sequence in self._pending:
            raise ValueError("sequence is already pending")
        self._pending[frame.sequence] = _Pending(frame, 0, float(now))
        return True

    def acknowledge(self, sequence):
        return self._pending.pop(sequence, None) is not None

    def due(self, now):
        now = float(now)
        due_frames = []
        for sequence, pending in list(self._pending.items()):
            if now < pending.next_due:
                continue
            if pending.attempts >= self.max_attempts:
                del self._pending[sequence]
                raise RetryExhausted(sequence)
            pending.attempts += 1
            pending.next_due = now + self.retry_interval
            due_frames.append(pending.frame)
        return due_frames


@dataclass
class MissionReceiveState:
    mission_id: str
    item_count: int
    vehicle: str
    items: dict = field(default_factory=dict)
    committed: bool = False

    @property
    def received_indices(self):
        return tuple(sorted(self.items))

    @property
    def missing_indices(self):
        return tuple(index for index in range(self.item_count) if index not in self.items)


class ReceiverState:
    def __init__(self, history_limit=1024):
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.history_limit = history_limit
        self._seen = OrderedDict()
        self._missions = {}

    def mission(self, mission_id):
        return self._missions.get(mission_id)

    def resume(self, mission_id):
        mission = self._require_mission(mission_id)
        return {"missing_indices": list(mission.missing_indices)}

    def accept(self, frame):
        if not isinstance(frame, Frame):
            raise TypeError("frame must be a Frame")
        validate_payload(frame.message_type, frame.payload)
        dedup_key = (frame.command_id, frame.sequence)
        if dedup_key in self._seen:
            return False

        if frame.message_type is MessageType.MISSION_BEGIN:
            self._begin(frame.payload)
        elif frame.message_type is MessageType.MISSION_ITEM:
            self._item(frame.payload)
        elif frame.message_type is MessageType.MISSION_COMMIT:
            self._commit(frame.payload)

        self._seen[dedup_key] = None
        self._seen.move_to_end(dedup_key)
        while len(self._seen) > self.history_limit:
            self._seen.popitem(last=False)
        return True

    def _require_mission(self, mission_id):
        mission = self._missions.get(mission_id)
        if mission is None:
            raise ValueError(f"mission {mission_id!r} has not begun")
        return mission

    def _begin(self, payload):
        mission_id = payload["mission_id"]
        existing = self._missions.get(mission_id)
        if existing is not None and (
            existing.item_count != payload["item_count"]
            or existing.vehicle != payload["vehicle"]
        ):
            raise ValueError("mission begin conflicts with existing mission")
        if existing is None:
            self._missions[mission_id] = MissionReceiveState(
                mission_id,
                payload["item_count"],
                payload["vehicle"],
            )

    def _item(self, payload):
        mission = self._require_mission(payload["mission_id"])
        index = payload["index"]
        if index >= mission.item_count:
            raise ValueError("mission item index is outside declared range")
        existing = mission.items.get(index)
        if existing is not None and existing != payload:
            raise ValueError("mission item conflicts with received item")
        mission.items[index] = dict(payload)

    def _commit(self, payload):
        mission = self._require_mission(payload["mission_id"])
        if payload["item_count"] != mission.item_count:
            raise ValueError("mission commit count does not match begin")
        if mission.missing_indices:
            raise ValueError("mission is incomplete")
        mission.committed = True
