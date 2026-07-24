from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json

from shared_protocol.frame import Frame, MessageType
from shared_protocol.messages import validate_payload


class InboxRejected(ValueError):
    def __init__(self, reason, *, missing=None):
        self.reason = str(reason)
        self.missing = list(missing or [])
        super().__init__(self.reason)


@dataclass(frozen=True)
class InboxResult:
    acked_sequence: int
    stage: str
    duplicate: bool = False


class DurableInbox:
    def __init__(
        self,
        store,
        *,
        max_queue=16,
        max_history=512,
        max_staged=8,
        max_resume_items=32,
        max_results=128,
    ):
        for name, value in (
            ("max_queue", max_queue),
            ("max_history", max_history),
            ("max_staged", max_staged),
            ("max_resume_items", max_resume_items),
            ("max_results", max_results),
        ):
            if int(value) < 1:
                raise ValueError(f"{name} must be positive")
        self.store = store
        self.max_queue = int(max_queue)
        self.max_history = int(max_history)
        self.max_staged = int(max_staged)
        self.max_resume_items = int(max_resume_items)
        self.max_results = int(max_results)
        self._state = self.store.load(
            {
                "version": 1,
                "active": None,
                "queue": [],
                "staged": {},
                "seen": [],
                "results": [],
            }
        )
        self._state.setdefault("results", [])
        for record in [
            self._state.get("active"),
            *self._state.get("queue", []),
        ]:
            if isinstance(record, dict) and record.get("kind") == "mission":
                record.setdefault("stage", "MISSION_STAGED")
        self._validate_loaded()

    @property
    def active(self):
        value = self._state["active"]
        return None if value is None else dict(value)

    @property
    def queue_depth(self):
        return len(self._state["queue"])

    @property
    def results(self):
        return [dict(result) for result in self._state["results"]]

    def accept(self, frame):
        if not isinstance(frame, Frame):
            raise TypeError("frame must be a Frame")
        validate_payload(frame.message_type, frame.payload)
        identity = f"{frame.command_id}:{frame.sequence}"
        fingerprint = self._fingerprint(frame)
        existing = next(
            (item for item in self._state["seen"] if item["id"] == identity),
            None,
        )
        if existing is not None:
            if existing["fingerprint"] != fingerprint:
                raise InboxRejected("duplicate_conflict")
            return InboxResult(
                frame.sequence, existing["stage"], duplicate=True
            )

        previous = copy.deepcopy(self._state)
        try:
            if frame.message_type is MessageType.COMMAND:
                stage = self._enqueue(self._command_record(frame))
            elif frame.message_type is MessageType.MISSION_BEGIN:
                stage = self._begin(frame)
            elif frame.message_type is MessageType.MISSION_ITEM:
                stage = self._item(frame)
            elif frame.message_type is MessageType.MISSION_COMMIT:
                stage = self._commit(frame)
            else:
                raise InboxRejected("unsupported_message")

            self._state["seen"].append(
                {"id": identity, "fingerprint": fingerprint, "stage": stage}
            )
            self._state["seen"] = self._state["seen"][-self.max_history :]
            self.store.save(self._state)
        except Exception:
            self._state = previous
            raise
        return InboxResult(frame.sequence, stage)

    def resume(self, command_id, mission_id):
        key = self._mission_key(command_id, mission_id)
        mission = self._state["staged"].get(key)
        if mission is None:
            raise InboxRejected("mission_not_found")
        received = {int(index) for index in mission["items"]}
        return [
            index
            for index in range(int(mission["item_count"]))
            if index not in received
        ][: self.max_resume_items]

    def complete_active(self):
        completed = self._state["active"]
        if completed is None:
            return None
        self._state["active"] = (
            self._state["queue"].pop(0) if self._state["queue"] else None
        )
        self.store.save(self._state)
        return dict(completed)

    def finish_active(self, stage, detail="", **extra):
        active = self._state["active"]
        if active is None:
            raise InboxRejected("no_active_command")
        result = {
            "command_id": active["command_id"],
            "stage": str(stage),
            "detail": str(detail)[:256],
            **extra,
        }
        previous = copy.deepcopy(self._state)
        try:
            self._state["results"].append(result)
            self._state["results"] = self._state["results"][
                -self.max_results :
            ]
            self._state["active"] = (
                self._state["queue"].pop(0)
                if self._state["queue"]
                else None
            )
            self.store.save(self._state)
        except Exception:
            self._state = previous
            raise
        return dict(result)

    def _begin(self, frame):
        key = self._mission_key(
            frame.command_id, frame.payload["mission_id"]
        )
        existing = self._state["staged"].get(key)
        candidate = {
            "command_id": str(frame.command_id),
            "mission_id": frame.payload["mission_id"],
            "item_count": int(frame.payload["item_count"]),
            "vehicle": frame.payload["vehicle"],
            "checksum": frame.payload.get("checksum", ""),
            "items": {},
        }
        if existing is not None:
            comparable = {**existing, "items": {}}
            if comparable != candidate:
                raise InboxRejected("mission_begin_conflict")
        elif len(self._state["staged"]) >= self.max_staged:
            raise InboxRejected("staging_full")
        else:
            self._state["staged"][key] = candidate
        return "MISSION_STAGING"

    def _item(self, frame):
        key = self._mission_key(
            frame.command_id, frame.payload["mission_id"]
        )
        mission = self._state["staged"].get(key)
        if mission is None:
            raise InboxRejected("mission_not_found")
        index = int(frame.payload["index"])
        if index >= int(mission["item_count"]):
            raise InboxRejected("item_out_of_range")
        existing = mission["items"].get(str(index))
        if existing is not None and existing != frame.payload:
            raise InboxRejected("mission_item_conflict")
        mission["items"][str(index)] = dict(frame.payload)
        return "MISSION_STAGING"

    def _commit(self, frame):
        key = self._mission_key(
            frame.command_id, frame.payload["mission_id"]
        )
        mission = self._state["staged"].get(key)
        if mission is None:
            raise InboxRejected("mission_not_found")
        if int(frame.payload["item_count"]) != int(mission["item_count"]):
            raise InboxRejected("count_mismatch")
        missing = self.resume(frame.command_id, frame.payload["mission_id"])
        if missing:
            raise InboxRejected("missing_items", missing=missing)
        items = [
            mission["items"][str(index)]
            for index in range(int(mission["item_count"]))
        ]
        checksum = hashlib.sha256(
            json.dumps(
                items,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        expected = frame.payload.get("checksum") or mission.get("checksum")
        if not expected or checksum != expected:
            raise InboxRejected("checksum_mismatch")
        record = {
            "kind": "mission",
            "command_id": str(frame.command_id),
            "mission_id": frame.payload["mission_id"],
            "items": items,
            "checksum": checksum,
            "stage": "MISSION_STAGED",
        }
        self._enqueue(record)
        del self._state["staged"][key]
        return "MISSION_STAGED"

    def _enqueue(self, record):
        if self._state["active"] is None:
            self._state["active"] = record
        elif len(self._state["queue"]) >= self.max_queue:
            raise InboxRejected("queue_full")
        else:
            self._state["queue"].append(record)
        return "COMMAND_STAGED"

    @staticmethod
    def _command_record(frame):
        return {
            "kind": "command",
            "command_id": str(frame.command_id),
            "action": frame.payload["action"],
            "parameters": dict(frame.payload.get("parameters", {})),
        }

    @staticmethod
    def _mission_key(command_id, mission_id):
        return f"{command_id}:{mission_id}"

    @staticmethod
    def _fingerprint(frame):
        return hashlib.sha256(
            json.dumps(
                {
                    "type": int(frame.message_type),
                    "command_id": str(frame.command_id),
                    "sequence": frame.sequence,
                    "payload": frame.payload,
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()

    def _validate_loaded(self):
        required = {
            "version", "active", "queue", "staged", "seen", "results"
        }
        if set(self._state) != required:
            raise ValueError("invalid inbox state fields")
        if not isinstance(self._state["queue"], list):
            raise ValueError("invalid inbox queue")
        if len(self._state["queue"]) > self.max_queue:
            raise ValueError("persisted queue exceeds limit")
        if not isinstance(self._state["staged"], dict):
            raise ValueError("invalid staged missions")
        if len(self._state["staged"]) > self.max_staged:
            raise ValueError("persisted staging exceeds limit")
        if not isinstance(self._state["seen"], list):
            raise ValueError("invalid inbox history")
        if len(self._state["seen"]) > self.max_history:
            raise ValueError("persisted history exceeds limit")
        if not isinstance(self._state["results"], list):
            raise ValueError("invalid inbox results")
        if len(self._state["results"]) > self.max_results:
            raise ValueError("persisted results exceed limit")
