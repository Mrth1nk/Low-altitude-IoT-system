from __future__ import annotations

import socket
import time
import uuid

from shared_protocol.node_messages import (
    NodeMessageError,
    build_ack_message,
    build_nack_message,
    build_status_message,
    decode_node_message,
    encode_node_message,
)

from .command_queue import CommandRejected


class SlaveUdpLink:
    """Typed rover/slave UDP endpoint; it never carries raw MAVLink."""

    def __init__(
        self,
        command_queue,
        state,
        *,
        peer_ip="192.168.4.2",
        peer_port=14610,
        local_port=14620,
        bind_ip="0.0.0.0",
        sock=None,
        clock=time.time,
        status_interval=1.0,
    ):
        self.command_queue = command_queue
        self.state = state
        self.peer = (str(peer_ip), int(peer_port))
        self.clock = clock
        self.status_interval = float(status_interval)
        self._status_sequence = 0
        self._last_status_at = None
        self._last_peer_at = 0.0
        self.socket = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.socket.bind((str(bind_ip), int(local_port)))

    def receive_available(self, *, max_datagrams=64):
        received = 0
        while received < int(max_datagrams):
            try:
                wire, peer = self.socket.recvfrom(65535)
            except (BlockingIOError, InterruptedError):
                break
            received += 1
            try:
                message = decode_node_message(wire)
            except NodeMessageError:
                continue
            if peer != self.peer:
                self._send_rejection(message, "invalid_peer", peer)
                continue
            if message["target"] != "aircraft_2":
                self._send_rejection(message, "invalid_target", peer)
                continue
            if message["source"] != "rover":
                self._send_rejection(message, "invalid_source", peer)
                continue
            self._last_peer_at = float(self.clock())
            try:
                accepted = self.command_queue.accept(message)
                reply = build_ack_message(
                    stage=accepted.stage,
                    duplicate=accepted.duplicate,
                    **self._reply_identity(message),
                )
            except CommandRejected as exc:
                reply = build_nack_message(
                    reason=exc.reason,
                    stage=exc.stage,
                    **self._reply_identity(message),
                )
            self._send(reply)
        return received

    def publish_due(self):
        now = float(self.clock())
        for result in self.command_queue.completed():
            original_stage = result.get("stage")
            stage = "VERIFIED" if original_stage in ("COMPLETED", "VERIFIED") else "FAILED"
            detail = str(result.get("detail", ""))[:160]
            event_type = "MISSION" if original_stage == "VERIFIED" else "COMMAND"
            record_event = getattr(self.state, "record_event", None)
            if callable(record_event):
                record_event(
                    event_type,
                    detail or stage,
                    sequence=int(result["sequence"]),
                    stage=stage,
                )
            message = build_ack_message(
                stage=stage,
                detail=detail,
                source="aircraft_2",
                target="rover",
                command_id=result["command_id"],
                sequence=int(result["sequence"]),
                timestamp=now,
            )
            self._send(message)
        if self._last_status_at is not None and now - self._last_status_at < self.status_interval:
            return False
        self._last_status_at = now
        message = build_status_message(
            source="aircraft_2",
            target="rover",
            command_id=str(uuid.uuid4()),
            sequence=self._status_sequence,
            timestamp=now,
            **self.state.snapshot(),
        )
        self._status_sequence = (self._status_sequence + 1) & 0xFFFFFFFF
        self._send(message)
        return True

    def peer_online(self, *, max_age=3.0):
        return self._last_peer_at > 0 and float(self.clock()) - self._last_peer_at <= float(max_age)

    def run_once(self):
        received = self.receive_available()
        self.publish_due()
        return received

    def close(self):
        self.socket.close()

    def _reply_identity(self, message):
        return {
            "source": "aircraft_2",
            "target": "rover",
            "command_id": message["command_id"],
            "sequence": message["sequence"],
            "timestamp": float(self.clock()),
        }

    def _send_rejection(self, message, reason, peer):
        reply = build_nack_message(
            reason=reason,
            stage="FAILED",
            **self._reply_identity(message),
        )
        self._send(reply, peer=peer)

    def _send(self, message, *, peer=None):
        self.socket.sendto(encode_node_message(message), peer or self.peer)
