from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
import threading
import uuid

from aircraft_agent.inbox import InboxRejected
from shared_protocol.frame import Frame, MessageType
from shared_protocol.node_messages import mission_digest, validate_node_message


class CommandRejected(ValueError):
    def __init__(self, reason, *, stage="FAILED", missing=None):
        self.reason = str(reason)
        self.stage = str(stage)
        self.missing = list(missing or [])
        super().__init__(self.reason)


@dataclass(frozen=True)
class QueueAcceptance:
    stage: str
    duplicate: bool
    sequence: int


class ThreadSafeInbox:
    """Serializes receiver and worker access to the existing durable inbox."""

    def __init__(self, inbox):
        self._inbox = inbox
        self._lock = threading.RLock()

    @property
    def active(self):
        with self._lock:
            return self._inbox.active

    @property
    def queue_depth(self):
        with self._lock:
            return self._inbox.queue_depth

    @property
    def results(self):
        with self._lock:
            return self._inbox.results

    def accept(self, frame):
        with self._lock:
            return self._inbox.accept(frame)

    def resume(self, command_id, mission_id):
        with self._lock:
            return self._inbox.resume(command_id, mission_id)

    def complete_active(self):
        with self._lock:
            return self._inbox.complete_active()

    def finish_active(self, stage, detail="", **extra):
        with self._lock:
            return self._inbox.finish_active(stage, detail, **extra)

    def staged_mission(self, command_id, mission_id):
        """Return a durable mission snapshot while holding the inbox lock."""
        key = f"{command_id}:{mission_id}"
        with self._lock:
            mission = self._inbox._state["staged"].get(key)
            return None if mission is None else copy.deepcopy(mission)

    def queued_mission(self, command_id, mission_id):
        with self._lock:
            records = [
                self._inbox._state.get("active"),
                *self._inbox._state.get("queue", []),
            ]
            for record in records:
                if (
                    isinstance(record, dict)
                    and record.get("kind") == "mission"
                    and record.get("command_id") == str(command_id)
                    and record.get("mission_id") == mission_id
                ):
                    return copy.deepcopy(record)
        return None


class NodeCommandQueue:
    """Validates node transactions and adapts them to durable LIOT frames."""

    def __init__(self, inbox, *, clock, max_age=3.0, max_future_skew=1.0):
        self.inbox = inbox
        self.clock = clock
        self.max_age = float(max_age)
        self.max_future_skew = float(max_future_skew)
        self._lock = threading.Lock()
        self._identities = {}

    def accept(self, message):
        message = validate_node_message(message, expected_target="aircraft_2")
        if message["source"] != "rover":
            raise CommandRejected("invalid_source")
        age = float(self.clock()) - float(message["timestamp"])
        if age > self.max_age:
            raise CommandRejected("expired")
        if age < -self.max_future_skew:
            raise CommandRejected("future_timestamp")
        try:
            with self._lock:
                result = self._accept_locked(message)
                if message["type"] in ("command", "mission_commit"):
                    self._identities[message["command_id"]] = {
                        "sequence": message["sequence"],
                        "reported": 0,
                    }
                return result
        except InboxRejected as exc:
            raise CommandRejected(exc.reason, missing=exc.missing) from exc
        except CommandRejected:
            raise
        except (TypeError, ValueError) as exc:
            raise CommandRejected(str(exc)) from exc

    def completed(self):
        """Return newly persisted worker results with original wire identity."""
        completed = []
        with self._lock:
            for result in self.inbox.results:
                identity = self._identities.get(result.get("command_id"))
                if identity is None or identity["reported"]:
                    continue
                identity["reported"] = 1
                completed.append({**result, "sequence": identity["sequence"]})
        return completed

    def _accept_locked(self, message):
        kind = message["type"]
        if kind == "command":
            return self._accept_frame(self._command_frame(message), "QUEUED")
        if kind == "mission_begin":
            return self._mission_begin(message)
        if kind == "mission_item":
            return self._mission_item(message)
        if kind == "mission_commit":
            return self._mission_commit(message)
        raise CommandRejected("unsupported_message")

    def _mission_begin(self, message):
        payload = message["payload"]
        if payload["item_count"] > 99:
            raise CommandRejected("mission_too_large")
        frame = self._frame(
            message,
            MessageType.MISSION_BEGIN,
            {
                "mission_id": payload["mission_id"],
                "item_count": payload["item_count"],
                "vehicle": "aircraft",
                "checksum": payload["digest"],
            },
        )
        return self._accept_frame(frame, "RECEIVED")

    def _mission_item(self, message):
        payload = message["payload"]
        mission = self.inbox.staged_mission(
            message["command_id"], payload["mission_id"]
        )
        if mission is None:
            raise CommandRejected("mission_not_found")
        if payload["index"] >= mission["item_count"]:
            raise CommandRejected("item_out_of_range")
        liot_payload = {"mission_id": payload["mission_id"], **payload["item"]}
        frame = self._frame(message, MessageType.MISSION_ITEM, liot_payload)
        return self._accept_frame(frame, "RECEIVED")

    def _mission_commit(self, message):
        payload = message["payload"]
        mission = self.inbox.staged_mission(
            message["command_id"], payload["mission_id"]
        )
        committed = mission is None
        if committed:
            mission = self.inbox.queued_mission(
                message["command_id"], payload["mission_id"]
            )
        if mission is None:
            raise CommandRejected("mission_not_found")
        stored_count = len(mission["items"]) if committed else mission["item_count"]
        if payload["item_count"] != stored_count:
            raise CommandRejected("count_mismatch")
        missing = [] if committed else [
            index for index in range(stored_count)
            if str(index) not in mission["items"]
        ]
        if missing:
            raise CommandRejected("missing_items", missing=missing[:32])
        liot_items = (
            list(mission["items"])
            if committed
            else [
                mission["items"][str(index)]
                for index in range(stored_count)
            ]
        )
        items = [
            {key: value for key, value in item.items() if key != "mission_id"}
            for item in liot_items
        ]
        expected_digest = mission_digest(items) if committed else mission.get("checksum", "")
        if (
            payload["digest"] != expected_digest
            or mission_digest(items) != expected_digest
        ):
            raise CommandRejected("digest_mismatch")
        checksum = hashlib.sha256(
            json.dumps(
                liot_items,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        frame = self._frame(
            message,
            MessageType.MISSION_COMMIT,
            {
                "mission_id": mission["mission_id"],
                "item_count": stored_count,
                "checksum": checksum,
            },
        )
        accepted = self._accept_frame(frame, "QUEUED")
        return accepted

    def _accept_frame(self, frame, stage):
        result = self.inbox.accept(frame)
        return QueueAcceptance(stage, result.duplicate, result.acked_sequence)

    @staticmethod
    def _command_frame(message):
        return NodeCommandQueue._frame(
            message,
            MessageType.COMMAND,
            {
                "action": message["payload"]["action"],
                "parameters": dict(message["payload"].get("parameters", {})),
            },
        )

    @staticmethod
    def _frame(message, message_type, payload):
        return Frame(
            message_type,
            0,
            message["sequence"],
            uuid.UUID(message["command_id"]),
            payload,
        )
