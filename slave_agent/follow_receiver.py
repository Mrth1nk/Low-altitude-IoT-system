"""Receive leader GLOBAL_POSITION_INT frames through the sole FC session."""

from __future__ import annotations

import socket
import threading
import time


FOLLOW_POSITION_MESSAGE_ID = 33


def _exact_mavlink2_follow_position(frame):
    frame = bytes(frame)
    if len(frame) < 12 or frame[0] != 0xFD:
        return False
    signature_length = 13 if frame[2] & 0x01 else 0
    expected_length = 10 + frame[1] + 2 + signature_length
    if len(frame) != expected_length:
        return False
    message_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
    return message_id == FOLLOW_POSITION_MESSAGE_ID and frame[5] == 1


class FollowTargetReceiver:
    def __init__(
        self,
        session,
        *,
        local_host="0.0.0.0",
        local_port=14630,
        rover_ip="192.168.4.2",
        sock=None,
        socket_factory=socket.socket,
        clock=time.monotonic,
    ):
        self.session = session
        self.rover_ip = str(rover_ip)
        self.clock = clock
        self.socket = sock or socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((str(local_host), int(local_port)))
        self.socket.setblocking(False)
        self._lock = threading.Lock()
        self._received = 0
        self._rejected = 0
        self._write_errors = 0
        self._last_target_at = None

    def run_once(self, max_receive=32):
        injected = 0
        for _ in range(int(max_receive)):
            try:
                frame, peer = self.socket.recvfrom(4096)
            except BlockingIOError:
                break
            except OSError:
                self._increment("write_errors")
                break
            if str(peer[0]) != self.rover_ip or not _exact_mavlink2_follow_position(frame):
                self._increment("rejected")
                continue
            try:
                self.session.write_raw(frame)
            except (OSError, RuntimeError):
                self._increment("write_errors")
                continue
            with self._lock:
                self._received += 1
                self._last_target_at = float(self.clock())
            injected += 1
        return injected

    def snapshot(self):
        with self._lock:
            return {
                "received": self._received,
                "rejected": self._rejected,
                "write_errors": self._write_errors,
                "last_target_age_s": (
                    None
                    if self._last_target_at is None
                    else round(max(0.0, float(self.clock()) - self._last_target_at), 3)
                ),
            }

    def close(self):
        self.socket.close()

    def _increment(self, name):
        with self._lock:
            if name == "rejected":
                self._rejected += 1
            elif name == "write_errors":
                self._write_errors += 1
