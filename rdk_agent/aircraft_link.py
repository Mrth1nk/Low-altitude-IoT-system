"""Reliable LIOT producer for commands sent from the RDK to the aircraft."""

import hashlib
import json

from shared_protocol.frame import Frame, MessageType, decode_frame, encode_frame
from shared_protocol.messages import MAX_MISSION_ITEMS
from shared_protocol.transport import RetrySender


class AircraftLink:
    def __init__(self, max_attempts=4, retry_interval=0.5):
        self.sender = RetrySender(max_attempts, retry_interval)
        self._next_sequence = 0
        self._stage = "idle"

    @property
    def pending_count(self):
        return self.sender.pending_count

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
        now = 0.0 if now is None else float(now)
        if command.action == "mission":
            return self._stage_mission(command, now)
        self._queue(
            MessageType.COMMAND,
            command.command_id,
            {"action": command.action, "parameters": dict(command.payload)},
            now,
        )
        self._stage = "queued"
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
        self._stage = "staged"
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
        if due:
            self._stage = "awaiting_ack"
        return [encode_frame(frame) for frame in due]

    def accept_ack(self, data):
        frame = decode_frame(data) if isinstance(data, (bytes, bytearray)) else data
        if not isinstance(frame, Frame) or frame.message_type is not MessageType.ACK:
            return False
        accepted = self.sender.acknowledge(
            frame.command_id, frame.payload["acked_sequence"]
        )
        if accepted and self.pending_count == 0:
            self._stage = "acknowledged"
        return accepted

    def transaction_state(self):
        return {"stage": self._stage, "pending": self.pending_count}
