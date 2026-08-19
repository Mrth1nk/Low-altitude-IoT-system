from __future__ import annotations

import json
import queue
import socket
import threading
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
        intake_capacity=64,
    ):
        if int(intake_capacity) < 1:
            raise ValueError("intake_capacity must be positive")
        self.command_queue = command_queue
        self.state = state
        self.peer = (str(peer_ip), int(peer_port))
        self.clock = clock
        self.status_interval = float(status_interval)
        self._status_sequence = 0
        self._last_status_at = None
        self._last_peer_at = 0.0
        self._send_lock = threading.Lock()
        self._intake = queue.Queue(maxsize=int(intake_capacity))
        self._intake_condition = threading.Condition()
        self._pending_intake = 0
        self._closing = False
        self.socket = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.socket.bind((str(bind_ip), int(local_port)))
        self._intake_worker = threading.Thread(
            target=self._persist_intake,
            name="slave-command-intake",
            daemon=True,
        )
        self._intake_worker.start()

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
                identity = self._recover_identity(wire, peer)
                if identity is not None:
                    self._send(build_nack_message(
                        reason="malformed_message",
                        stage="FAILED",
                        **identity,
                    ))
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
                self._enqueue(message)
            except queue.Full:
                self._send(build_nack_message(
                    reason="intake_full",
                    stage="FAILED",
                    **self._reply_identity(message),
                ))
        return received

    def wait_for_intake(self, *, timeout=None):
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        with self._intake_condition:
            while self._pending_intake:
                if deadline is None:
                    self._intake_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._intake_condition.wait(remaining)
        return True

    def publish_due(self):
        now = float(self.clock())
        for result in self.command_queue.completed():
            original_stage = result.get("stage")
            stage = "VERIFIED" if original_stage in ("COMPLETED", "VERIFIED") else "FAILED"
            detail = str(result.get("detail", ""))[:160]
            mission_id = str(result.get("mission_id", ""))
            event_type = "MISSION" if mission_id else "COMMAND"
            record_event = getattr(self.state, "record_event", None)
            if callable(record_event):
                record_event(
                    event_type,
                    detail or stage,
                    sequence=int(result["sequence"]),
                    mission_id=mission_id,
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
            self.command_queue.mark_delivered(result)
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
        if self._closing:
            return
        self._closing = True
        self.wait_for_intake()
        self._intake.put(None)
        self._intake_worker.join()
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
        wire = encode_node_message(message)
        with self._send_lock:
            self.socket.sendto(wire, peer or self.peer)

    def _enqueue(self, message):
        with self._intake_condition:
            if self._closing:
                raise queue.Full
            self._pending_intake += 1
            try:
                self._intake.put_nowait(message)
            except queue.Full:
                self._pending_intake -= 1
                raise

    def _persist_intake(self):
        while True:
            message = self._intake.get()
            if message is None:
                self._intake.task_done()
                return
            try:
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
                except Exception as exc:
                    set_fault = getattr(self.state, "set_fault", None)
                    if callable(set_fault):
                        set_fault(f"command_persistence: {type(exc).__name__}: {exc}")
                    reply = build_nack_message(
                        reason="persistence_failed",
                        stage="FAILED",
                        **self._reply_identity(message),
                    )
                try:
                    self._send(reply)
                except OSError as exc:
                    set_fault = getattr(self.state, "set_fault", None)
                    if callable(set_fault):
                        set_fault(f"udp_send: {type(exc).__name__}: {exc}")
            finally:
                self._intake.task_done()
                with self._intake_condition:
                    self._pending_intake -= 1
                    self._intake_condition.notify_all()

    def _recover_identity(self, wire, peer):
        if peer != self.peer:
            return None
        try:
            message = json.loads(bytes(wire).decode("utf-8"))
            if not isinstance(message, dict):
                return None
            if message.get("source") != "rover" or message.get("target") != "aircraft_2":
                return None
            command_id = str(message["command_id"])
            uuid.UUID(command_id)
            sequence = message["sequence"]
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                return None
            if not 0 <= sequence <= 0xFFFFFFFF:
                return None
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
        ):
            return None
        return {
            "source": "aircraft_2",
            "target": "rover",
            "command_id": command_id,
            "sequence": sequence,
            "timestamp": float(self.clock()),
        }
