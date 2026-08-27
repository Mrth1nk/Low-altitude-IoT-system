"""Relay fresh leader GLOBAL_POSITION_INT frames to the follower RDK."""

from __future__ import annotations

import socket
import threading
import time


FOLLOW_POSITION_MESSAGE_ID = 33


class _MavlinkFrameStream:
    def __init__(self, *, max_buffer=65536):
        self.buffer = bytearray()
        self.max_buffer = int(max_buffer)

    def feed(self, chunk):
        self.buffer.extend(bytes(chunk))
        frames = []
        offset = 0
        while offset <= len(self.buffer) - 8:
            magic = self.buffer[offset]
            if magic not in (0xFD, 0xFE):
                offset += 1
                continue
            payload_length = self.buffer[offset + 1]
            is_v2 = magic == 0xFD
            header_length = 10 if is_v2 else 6
            signed_length = (
                13
                if is_v2 and (self.buffer[offset + 2] & 0x01)
                else 0
            )
            frame_length = header_length + payload_length + 2 + signed_length
            if offset + frame_length > len(self.buffer):
                break
            frames.append(bytes(self.buffer[offset:offset + frame_length]))
            offset += frame_length
        if offset:
            del self.buffer[:offset]
        if len(self.buffer) > self.max_buffer:
            del self.buffer[:-10]
        return frames


class FollowTargetRelay:
    def __init__(
        self,
        *,
        peer=("192.168.4.3", 14630),
        socket_factory=socket.socket,
        clock=time.monotonic,
        max_age_s=1.5,
    ):
        self.peer = (str(peer[0]), int(peer[1]))
        self.clock = clock
        self.max_age_s = float(max_age_s)
        self.socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.parser = _MavlinkFrameStream()
        self._lock = threading.Lock()
        self._sent = 0
        self._rejected = 0
        self._stale = 0
        self._send_errors = 0
        self._last_frame_at = None

    def observe(self, data, _remote=None, *, observed_at=None):
        observed_at = float(self.clock() if observed_at is None else observed_at)
        now = float(self.clock())
        sent = 0
        for frame in self.parser.feed(data):
            if frame[0] != 0xFD:
                self._increment("rejected")
                continue
            message_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
            source_system = frame[5]
            if message_id != FOLLOW_POSITION_MESSAGE_ID or source_system != 1:
                self._increment("rejected")
                continue
            if now - observed_at > self.max_age_s:
                self._increment("stale")
                continue
            try:
                self.socket.sendto(frame, self.peer)
            except OSError:
                self._increment("send_errors")
                continue
            with self._lock:
                self._sent += 1
                self._last_frame_at = now
            sent += 1
        return sent

    def snapshot(self):
        with self._lock:
            return {
                "peer": f"{self.peer[0]}:{self.peer[1]}",
                "sent": self._sent,
                "rejected": self._rejected,
                "stale": self._stale,
                "send_errors": self._send_errors,
                "last_frame_age_s": (
                    None
                    if self._last_frame_at is None
                    else round(max(0.0, float(self.clock()) - self._last_frame_at), 3)
                ),
            }

    def close(self):
        self.socket.close()

    def _increment(self, name):
        with self._lock:
            if name == "rejected":
                self._rejected += 1
            elif name == "stale":
                self._stale += 1
            elif name == "send_errors":
                self._send_errors += 1
