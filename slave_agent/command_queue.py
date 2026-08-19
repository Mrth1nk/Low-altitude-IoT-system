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
        self._active_callback = None
        self._executing = set()

    def set_active_callback(self, callback):
        self._active_callback = callback

    @property
    def active(self):
        with self._lock:
            active = self._inbox.active
            if active is None:
                return None
            command_id = active.get("command_id", "")
            notify = command_id not in self._executing
            if notify:
                self._executing.add(command_id)
            callback = self._active_callback
        if notify and callback is not None:
            callback(active)
        return active

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

    def contains_command(self, command_id):
        command_id = str(command_id)
        with self._lock:
            records = [
                self._inbox._state.get("active"),
                *self._inbox._state.get("queue", []),
                *self._inbox._state.get("results", []),
            ]
            return any(
                isinstance(record, dict)
                and record.get("command_id") == command_id
                for record in records
            )


class NodeCommandQueue:
    """Validates node transactions and adapts them to durable LIOT frames."""

    def __init__(
        self,
        inbox,
        *,
        clock,
        max_age=3.0,
        max_future_skew=1.0,
        delivery_store=None,
        stage_callback=None,
        optical_gate=None,
        max_delivery_records=512,
    ):
        self.inbox = inbox
        self.clock = clock
        self.max_age = float(max_age)
        self.max_future_skew = float(max_future_skew)
        self._lock = threading.Lock()
        self._identities = {}
        self.delivery_store = delivery_store
        self.stage_callback = stage_callback
        self.optical_gate = optical_gate
        self.max_delivery_records = int(max_delivery_records)
        self._delivery = (
            {"version": 1, "commands": {}}
            if delivery_store is None
            else delivery_store.load({"version": 1, "commands": {}})
        )
        self._delivery.setdefault("commands", {})
        self._reconcile_delivery()
        self.inbox.set_active_callback(self._active_started)

    def accept(self, message):
        message = validate_node_message(message, expected_target="aircraft_2")
        if message["source"] != "rover":
            raise CommandRejected("invalid_source")
        if self.optical_gate is not None and not self.optical_gate.allows_commands():
            raise CommandRejected("optical_blocked")
        age = float(self.clock()) - float(message["timestamp"])
        if age > self.max_age:
            raise CommandRejected("expired")
        if age < -self.max_future_skew:
            raise CommandRejected("future_timestamp")
        try:
            with self._lock:
                self._emit_stage("RECEIVED", message)
                tracked = message["type"] in ("command", "mission_commit")
                if tracked:
                    duplicate = self._deduplicate_delivery(message)
                    if duplicate is not None:
                        self._emit_stage(duplicate.stage, message)
                        return duplicate
                previous_delivery = None
                if tracked:
                    previous_delivery = copy.deepcopy(self._delivery)
                    self._record_delivery(message, "QUEUED")
                try:
                    result = self._accept_locked(message)
                except Exception:
                    if previous_delivery is not None:
                        self._delivery = previous_delivery
                        self._save_delivery()
                    raise
                self._emit_stage(result.stage, message)
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
            dirty = False
            for result in self.inbox.results:
                command_id = result.get("command_id")
                record = self._delivery["commands"].get(command_id)
                if record is None:
                    continue
                terminal = "VERIFIED" if result.get("stage") in ("COMPLETED", "VERIFIED") else "FAILED"
                changed = record.get("terminal_stage") != terminal
                updated = {
                    "terminal_stage": terminal,
                    "worker_stage": result.get("stage", ""),
                    "detail": str(result.get("detail", "")),
                    "delivered": False if changed else bool(record.get("delivered", False)),
                }
                if any(record.get(key) != value for key, value in updated.items()):
                    record.update(updated)
                    dirty = True
                if changed:
                    self._emit_stage(
                        terminal,
                        {
                            "command_id": command_id,
                            "sequence": record["sequence"],
                            "payload": {"mission_id": record.get("mission_id", "")},
                        },
                    )
            if dirty:
                self._save_delivery()
            for command_id, record in self._delivery["commands"].items():
                if not record.get("terminal_stage") or record.get("delivered"):
                    continue
                completed.append({
                    "command_id": command_id,
                    "sequence": record["sequence"],
                    "stage": record.get("worker_stage") or record["terminal_stage"],
                    "detail": record.get("detail", ""),
                    "mission_id": record.get("mission_id", ""),
                })
        return completed

    def mark_delivered(self, result):
        with self._lock:
            record = self._delivery["commands"].get(str(result["command_id"]))
            if record is None:
                return False
            record["delivered"] = True
            self._save_delivery()
            return True

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

    def _deduplicate_delivery(self, message):
        record = self._delivery["commands"].get(message["command_id"])
        if record is None:
            return None
        fingerprint = self._delivery_fingerprint(message)
        if record.get("fingerprint") != fingerprint:
            raise CommandRejected("duplicate_conflict")
        changed = record.get("sequence") != message["sequence"]
        record["sequence"] = message["sequence"]
        if record.get("terminal_stage"):
            changed = changed or bool(record.get("delivered"))
            record["delivered"] = False
        if changed:
            record["updated_at"] = float(self.clock())
            self._save_delivery()
        return QueueAcceptance(
            record.get("terminal_stage") or record.get("stage", "QUEUED"),
            True,
            message["sequence"],
        )

    def _record_delivery(self, message, stage):
        command_id = message["command_id"]
        mission_id = message.get("payload", {}).get("mission_id", "")
        previous = copy.deepcopy(self._delivery)
        try:
            self._delivery["commands"][command_id] = {
                "sequence": message["sequence"],
                "stage": stage,
                "mission_id": mission_id,
                "message_type": message["type"],
                "fingerprint": self._delivery_fingerprint(message),
                "terminal_stage": "",
                "worker_stage": "",
                "detail": "",
                "delivered": False,
                "updated_at": float(self.clock()),
            }
            self._trim_delivery()
            self._save_delivery()
        except Exception:
            self._delivery = previous
            raise

    def _active_started(self, record):
        self._emit_stage(
            "EXECUTING",
            {
                "command_id": record.get("command_id", ""),
                "sequence": self._delivery["commands"].get(
                    record.get("command_id", ""), {}
                ).get("sequence", 0),
                "payload": {"mission_id": record.get("mission_id", "")},
            },
        )

    def _emit_stage(self, stage, message):
        if self.stage_callback is None:
            return
        payload = message.get("payload", {})
        self.stage_callback({
            "stage": str(stage),
            "command_id": str(message.get("command_id", "")),
            "sequence": int(message.get("sequence", 0)),
            "mission_id": str(payload.get("mission_id", "")),
        })

    def _save_delivery(self):
        if self.delivery_store is not None:
            self.delivery_store.save(self._delivery)

    def _trim_delivery(self):
        commands = self._delivery["commands"]
        if len(commands) <= self.max_delivery_records:
            return
        oldest = sorted(
            commands,
            key=lambda command_id: commands[command_id].get("updated_at", 0.0),
        )[: len(commands) - self.max_delivery_records]
        for command_id in oldest:
            del commands[command_id]

    @staticmethod
    def _command_fingerprint(message):
        return hashlib.sha256(
            json.dumps(
                message["payload"],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _delivery_fingerprint(message):
        if message["type"] == "command":
            return NodeCommandQueue._command_fingerprint(message)
        return hashlib.sha256(
            json.dumps(
                {
                    "type": message["type"],
                    "payload": message["payload"],
                },
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    def _reconcile_delivery(self):
        commands = self._delivery["commands"]
        orphaned = [
            command_id
            for command_id, record in commands.items()
            if not record.get("terminal_stage")
            and not self.inbox.contains_command(command_id)
        ]
        if not orphaned:
            return
        for command_id in orphaned:
            del commands[command_id]
        self._save_delivery()

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


class VerifiedOperations:
    """Requires a post-command heartbeat before mode/arm succeeds."""

    def __init__(self, delegate, state, *, verification_timeout=2.0):
        self.delegate = delegate
        self.state = state
        self.verification_timeout = float(verification_timeout)

    def set_mode(self, mode):
        token = self.state.heartbeat_token()
        result = self.delegate.set_mode(mode)
        if not self.state.wait_for_heartbeat(
            token,
            lambda snapshot: snapshot["mode"] == str(mode).upper(),
            timeout=self.verification_timeout,
        ):
            raise TimeoutError(f"fresh heartbeat did not confirm mode {mode}")
        return result

    def arm(self, value):
        token = self.state.heartbeat_token()
        result = self.delegate.arm(value)
        if not self.state.wait_for_heartbeat(
            token,
            lambda snapshot: snapshot["armed"] is bool(value),
            timeout=self.verification_timeout,
        ):
            raise TimeoutError("fresh heartbeat did not confirm armed state")
        return result

    def execute(self, record, start_auto=False):
        return self.delegate.execute(record, start_auto=start_auto)
