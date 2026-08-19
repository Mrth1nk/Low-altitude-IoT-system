"""Reliable typed UDP coordinator for the RDK-X5 slave aircraft."""

from __future__ import annotations

import socket
import time
import uuid
from collections import deque

from shared_protocol.node_messages import (
    NodeMessageError,
    build_command_message,
    build_mission_begin_message,
    build_mission_commit_message,
    build_mission_item_message,
    decode_node_message,
    encode_node_message,
    mission_digest,
)


class SlaveUnavailable(ValueError):
    """Raised when a new slave command cannot be accepted safely."""


class SlaveTransport:
    """Owns the rover side of the typed aircraft_2 transaction channel."""

    TERMINAL_STAGES = frozenset(("VERIFIED", "FAILED"))

    def __init__(
        self,
        *,
        local_host="0.0.0.0",
        local_port=14610,
        peer=("192.168.4.3", 14620),
        sock=None,
        socket_factory=socket.socket,
        wall_clock=time.time,
        monotonic_clock=time.monotonic,
        retry_interval=0.5,
        status_timeout=3.0,
        max_pending=64,
        max_attempts=6,
        response_history_limit=256,
    ):
        if peer is None:
            raise ValueError("an exact slave peer is required")
        if (
            retry_interval <= 0
            or status_timeout <= 0
            or max_pending <= 0
            or max_attempts <= 0
            or response_history_limit <= 0
        ):
            raise ValueError("invalid slave transport limits")
        self.peer = (str(peer[0]), int(peer[1]))
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.retry_interval = float(retry_interval)
        self.status_timeout = float(status_timeout)
        self.max_pending = int(max_pending)
        self.max_attempts = int(max_attempts)
        self.socket = sock or socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.socket.bind((str(local_host), int(local_port)))
        self._sequence = 0
        self._pending = {}
        self._last_status_at = None
        self._last_status = self._offline_state()
        self._seen_responses = set()
        self._response_history = deque(maxlen=int(response_history_limit))
        self._diagnostics = {
            "rx_packets": 0,
            "rx_wrong_peer": 0,
            "rx_invalid": 0,
            "rx_duplicates": 0,
            "tx_packets": 0,
            "last_error": "",
        }

    def close(self):
        self.socket.close()

    def execute(self, command):
        if command.target != "aircraft_2":
            raise ValueError("SlaveTransport accepts aircraft_2 commands only")
        state = self.snapshot()
        if not state["online"]:
            raise SlaveUnavailable("aircraft_2 offline")
        if state.get("blocked"):
            raise SlaveUnavailable("aircraft_2 optical link blocked")
        command_id = str(command.command_id)
        if command_id in self._pending:
            return {"accepted": True, "stage": "QUEUED", "message": "duplicate pending command"}
        if len(self._pending) >= self.max_pending:
            raise SlaveUnavailable("aircraft_2 command queue full")
        messages = self._messages_for(command)
        wires = tuple(encode_node_message(message) for message in messages)
        now = float(self.monotonic_clock())
        self._pending[command_id] = {
            "wires": wires,
            "next_retry": now + self.retry_interval,
            "stage": "QUEUED",
            "mission_id": str(command.payload.get("mission_id", "")),
            "attempts": 1,
        }
        self._send_wires(wires)
        return {
            "accepted": True,
            "stage": "QUEUED",
            "message": f"aircraft_2 {command.action} queued",
        }

    def pump(self, now=None, max_receive=64):
        now = float(self.monotonic_clock() if now is None else now)
        self._receive(max_receive, now)
        if (
            self._last_status_at is not None
            and now - self._last_status_at > self.status_timeout
        ):
            self._fail_pending("status timeout")
        for command_id, entry in tuple(self._pending.items()):
            if now >= entry["next_retry"]:
                if entry["attempts"] >= self.max_attempts:
                    self._fail_transaction(command_id, "retry budget exhausted")
                    continue
                self._send_wires(entry["wires"])
                entry["attempts"] += 1
                entry["next_retry"] = now + self.retry_interval
        return self.snapshot(now=now)

    def snapshot(self, now=None):
        now = float(self.monotonic_clock() if now is None else now)
        state = dict(self._last_status)
        state["event"] = dict(self._last_status.get("event", {}))
        online = (
            self._last_status_at is not None
            and now - self._last_status_at <= self.status_timeout
        )
        state["online"] = bool(online and state.get("online", False))
        if not online:
            state["link_state"] = "OFFLINE"
        state["updated_at"] = (
            round(float(state.get("updated_at", 0.0)), 3)
            if self._last_status_at is not None
            else 0.0
        )
        state["age"] = (
            round(max(0.0, now - self._last_status_at), 2)
            if self._last_status_at is not None
            else None
        )
        return state

    def diagnostics(self):
        return {
            **self._diagnostics,
            "pending": len(self._pending),
            "response_history": len(self._response_history),
        }

    def _messages_for(self, command):
        identity = {
            "source": "rover",
            "target": "aircraft_2",
            "command_id": str(command.command_id),
            "timestamp": float(self.wall_clock()),
        }
        if command.action != "mission":
            return [build_command_message(
                action=str(command.action),
                parameters=dict(command.payload),
                sequence=self._next_sequence(),
                **identity,
            )]
        raw_items = command.payload.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("aircraft_2 mission requires items")
        if len(raw_items) > 99:
            raise ValueError("aircraft_2 mission supports at most 99 waypoints")
        items = [self._mission_item(item, index) for index, item in enumerate(raw_items)]
        mission_id = str(command.payload.get("mission_id") or command.command_id)
        digest = mission_digest(items)
        common = {
            **identity,
            "mission_id": mission_id,
            "item_count": len(items),
            "digest": digest,
        }
        messages = [build_mission_begin_message(sequence=self._next_sequence(), **common)]
        messages.extend(
            build_mission_item_message(
                mission_id=mission_id,
                index=index,
                item=item,
                sequence=self._next_sequence(),
                **identity,
            )
            for index, item in enumerate(items)
        )
        messages.append(build_mission_commit_message(sequence=self._next_sequence(), **common))
        return messages

    @staticmethod
    def _mission_item(raw, index):
        if not isinstance(raw, dict):
            raise ValueError("mission item must be an object")
        lon = raw.get("lon", raw.get("lng"))
        item = {
            "index": index,
            "lat": float(raw["lat"]),
            "lon": float(lon),
            "alt": float(raw.get("alt", raw.get("altitude", raw.get("target_alt", 0)))),
        }
        for key in ("command", "frame", "param1", "param2", "param3", "param4", "autocontinue"):
            if key in raw:
                item[key] = raw[key]
        return item

    def _receive(self, max_receive, now):
        for _ in range(int(max_receive)):
            try:
                wire, peer = self.socket.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError as exc:
                self._diagnostics["last_error"] = f"receive: {exc}"[:120]
                return
            self._diagnostics["rx_packets"] += 1
            if (str(peer[0]), int(peer[1])) != self.peer:
                self._diagnostics["rx_wrong_peer"] += 1
                continue
            try:
                message = decode_node_message(wire, expected_target="rover")
            except NodeMessageError:
                self._diagnostics["rx_invalid"] += 1
                continue
            if message["source"] != "aircraft_2":
                self._diagnostics["rx_invalid"] += 1
                continue
            if message["type"] == "status":
                self._last_status = {**message["payload"], "updated_at": message["timestamp"]}
                self._last_status_at = now
                continue
            if message["type"] not in ("ack", "nack"):
                self._diagnostics["rx_invalid"] += 1
                continue
            response_key = (
                message["command_id"], message["sequence"], message["type"],
                message["payload"].get("stage"), message["payload"].get("reason"),
            )
            if response_key in self._seen_responses:
                self._diagnostics["rx_duplicates"] += 1
                continue
            if message["command_id"] not in self._pending:
                self._diagnostics["rx_invalid"] += 1
                continue
            self._remember_response(response_key)
            self._apply_response(message)

    def _apply_response(self, message):
        command_id = message["command_id"]
        entry = self._pending.get(command_id)
        stage = (
            message["payload"].get("stage", "FAILED")
            if message["type"] == "ack"
            else "FAILED"
        )
        detail = str(
            message["payload"].get("detail", message["payload"].get("reason", ""))
        )[:160]
        mission_id = entry.get("mission_id", "") if entry else ""
        if entry is not None:
            entry["stage"] = stage
            if stage in self.TERMINAL_STAGES:
                self._pending.pop(command_id, None)
        self._last_status["mission_stage"] = stage
        if mission_id:
            self._last_status["mission_id"] = mission_id
        self._last_status["fault"] = detail if stage == "FAILED" else ""
        self._last_status["event"] = {
            "timestamp": float(message["timestamp"]),
            "sequence": int(message["sequence"]),
            "type": "MISSION" if mission_id else "COMMAND",
            "text": detail or stage,
        }

    def _send_wires(self, wires):
        for wire in wires:
            try:
                self.socket.sendto(wire, self.peer)
                self._diagnostics["tx_packets"] += 1
            except OSError as exc:
                self._diagnostics["last_error"] = f"send: {exc}"[:120]

    def _remember_response(self, response_key):
        if len(self._response_history) == self._response_history.maxlen:
            expired = self._response_history.popleft()
            self._seen_responses.discard(expired)
        self._response_history.append(response_key)
        self._seen_responses.add(response_key)

    def _fail_pending(self, reason):
        for command_id in tuple(self._pending):
            self._fail_transaction(command_id, reason)

    def _fail_transaction(self, command_id, reason):
        entry = self._pending.pop(command_id, None)
        if entry is None:
            return
        self._last_status["mission_stage"] = "FAILED"
        if entry.get("mission_id"):
            self._last_status["mission_id"] = entry["mission_id"]
        self._last_status["fault"] = str(reason)[:160]
        self._last_status["event"] = {
            "timestamp": float(self.wall_clock()),
            "sequence": 0,
            "type": "MISSION" if entry.get("mission_id") else "COMMAND",
            "text": str(reason)[:160],
        }

    def _next_sequence(self):
        result = self._sequence
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        return result

    @staticmethod
    def _offline_state():
        return {
            "online": False,
            "fc_connected": False,
            "blocked": False,
            "mode": "UNKNOWN",
            "armed": False,
            "heartbeat_at": 0.0,
            "lat": 0.0,
            "lon": 0.0,
            "position_observed": False,
            "altitude": 0.0,
            "speed": 0.0,
            "heading": 0.0,
            "battery": -1.0,
            "mission_stage": "IDLE",
            "mission_id": "",
            "fault": "",
            "link_state": "OFFLINE",
            "event": {"timestamp": 0.0, "sequence": 0, "type": "NONE", "text": ""},
            "updated_at": 0.0,
        }
