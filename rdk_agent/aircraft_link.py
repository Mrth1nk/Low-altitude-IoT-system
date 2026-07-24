"""Reliable LIOT producer for commands sent from the RDK to the aircraft."""

import hashlib
import json
from collections import OrderedDict

from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame
from shared_protocol.messages import MAX_MISSION_ITEMS
from shared_protocol.transport import RetrySender


class AircraftLink:
    def __init__(
        self,
        max_attempts=4,
        retry_interval=0.5,
        history_limit=128,
    ):
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self.sender = RetrySender(max_attempts, retry_interval)
        self._next_sequence = 0
        self._stage = "idle"
        self._error = ""
        self._transaction_id = ""
        self._active_command_id = None
        self._revision = 0
        self._terminal = OrderedDict()
        self._terminal_limit = int(history_limit)

    @property
    def pending_count(self):
        return self.sender.pending_count

    @property
    def active_command_id(self):
        return self._active_command_id

    def _sequence(self):
        result = self._next_sequence
        self._next_sequence = (self._next_sequence + 1) & 0xFFFFFFFF
        return result

    def _queue(self, message_type, command_id, payload, now):
        frame = Frame(message_type, 0, self._sequence(), command_id, payload)
        self.sender.queue(frame, now)
        return frame

    def execute(self, command, now=None):
        if command.target != "aircraft":
            raise ValueError("AircraftLink accepts aircraft commands only")
        if self._active_command_id is not None:
            raise ValueError("aircraft transaction already active")
        if command.command_id in self._terminal:
            raise ValueError("aircraft transaction is already terminal")
        now = 0.0 if now is None else float(now)
        self._active_command_id = command.command_id
        self._transaction_id = str(command.command_id)
        self._error = ""
        if command.action == "mission":
            return self._stage_mission(command, now)
        self._queue(
            MessageType.COMMAND,
            command.command_id,
            {"action": command.action, "parameters": dict(command.payload)},
            now,
        )
        self._set_stage("queued")
        return {"stage": "queued", "frame_count": 1}

    def _stage_mission(self, command, now):
        mission_id = str(command.payload.get("mission_id", "")).strip()
        items = command.payload.get("items")
        if not mission_id:
            raise ValueError("mission_id must not be empty")
        if not isinstance(items, list):
            raise TypeError("mission items must be a list")
        if len(items) > MAX_MISSION_ITEMS:
            raise ValueError("item_count exceeds mission limit")

        normalized = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise TypeError("mission item must be an object")
            normalized.append(self._mission_item(mission_id, index, item))
        checksum = hashlib.sha256(
            json.dumps(
                normalized, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        ).hexdigest()

        self._queue(
            MessageType.MISSION_BEGIN,
            command.command_id,
            {
                "mission_id": mission_id,
                "item_count": len(normalized),
                "vehicle": "aircraft",
                "checksum": checksum,
            },
            now,
        )
        for item in normalized:
            self._queue(
                MessageType.MISSION_ITEM, command.command_id, item, now
            )
        self._queue(
            MessageType.MISSION_COMMIT,
            command.command_id,
            {
                "mission_id": mission_id,
                "item_count": len(normalized),
                "checksum": checksum,
            },
            now,
        )
        self._set_stage("staged")
        return {"stage": "staged", "frame_count": len(normalized) + 2}

    @staticmethod
    def _mission_item(mission_id, index, item):
        return {
            "mission_id": mission_id,
            "index": index,
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "alt": float(item["alt"]),
            "command": int(item.get("command", 16)),
            "frame": int(item.get("frame", 6)),
            "param1": float(item.get("param1", 0.0)),
            "param2": float(
                item.get("param2", item.get("acceptance_radius", 0.0))
            ),
            "param3": float(item.get("param3", 0.0)),
            "param4": float(item.get("param4", 0.0)),
            "autocontinue": bool(item.get("autocontinue", True)),
        }

    def due_bytes(self, now):
        due = self.sender.due(now)
        exhausted = self.sender.pop_exhausted()
        if due and not exhausted:
            self._set_stage("awaiting_ack")
        if exhausted:
            for command_id, sequence in exhausted:
                self._set_terminal(
                    command_id,
                    "retry_exhausted",
                    f"command {command_id} sequence {sequence} retry exhausted",
                )
            due = [
                frame for frame in due if frame.command_id not in self._terminal
            ]
        return [encode_frame(frame) for frame in due]

    def accept_ack(self, data):
        frame = decode_frame(data) if isinstance(data, (bytes, bytearray)) else data
        if not isinstance(frame, Frame) or frame.message_type is not MessageType.ACK:
            return False
        if frame.command_id in self._terminal:
            return False
        accepted = self.sender.acknowledge(
            frame.command_id, frame.payload["acked_sequence"]
        )
        if (
            accepted
            and self.pending_count == 0
            and frame.command_id == self._active_command_id
        ):
            self._set_terminal(frame.command_id, "acknowledged", "")
        return accepted

    def accept_response(self, frame):
        if not isinstance(frame, Frame):
            raise TypeError("response must be a Frame")
        if frame.message_type is MessageType.ACK:
            return self.accept_ack(frame)
        if frame.message_type is not MessageType.NACK:
            return False
        if frame.command_id in self._terminal:
            return False
        accepted = self.sender.acknowledge(
            frame.command_id, frame.payload["acked_sequence"]
        )
        if accepted:
            self._set_terminal(
                frame.command_id, "nacked", str(frame.payload["reason"])[:120]
            )
        return accepted

    def fail_transaction(self, stage, error, command_id=None):
        if stage not in ("retry_exhausted", "link_blocked"):
            raise ValueError("invalid terminal transaction stage")
        command_id = command_id or self._active_command_id
        if command_id is None:
            return False
        if command_id in self._terminal:
            return False
        self._set_terminal(command_id, stage, str(error)[:120])
        return True

    def _set_terminal(self, command_id, stage, error):
        self.sender.cancel(command_id)
        self._revision += 1
        self._terminal[command_id] = {
            "command_id": str(command_id),
            "stage": stage,
            "error": error,
            "revision": self._revision,
        }
        self._terminal.move_to_end(command_id)
        while len(self._terminal) > self._terminal_limit:
            self._terminal.popitem(last=False)
        if str(command_id) == self._transaction_id:
            self._stage = stage
            self._error = error
        if command_id == self._active_command_id:
            self._active_command_id = None

    def _set_stage(self, stage):
        if stage != self._stage:
            self._stage = stage
            self._revision += 1

    def event_history(self):
        return [dict(item) for item in self._terminal.values()]

    def transaction_state(self):
        state = {
            "stage": self._stage,
            "pending": self.pending_count,
            "transaction_id": self._transaction_id,
            "revision": self._revision,
        }
        if self._error:
            state["error"] = self._error
        return state
