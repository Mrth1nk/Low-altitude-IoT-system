"""Build fresh leader state into standard MAVLink2 FOLLOW_TARGET frames."""

from __future__ import annotations

import math
import threading
import time

from .telemetry import field, message_type


FOLLOW_TARGET_MESSAGE_ID = 144
FOLLOW_TARGET_CAPABILITIES = 1 | 2 | 8 | 16


def _source_system(message):
    if isinstance(message, dict):
        return int(message.get("source_system", message.get("sysid", 0)) or 0)
    getter = getattr(message, "get_srcSystem", None)
    return int(getter() if callable(getter) else 0)


def _euler_quaternion(roll, pitch, yaw):
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return (
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )


class PymavlinkFollowTargetEncoder:
    """Keep MAVLink packet sequence ownership inside one publisher."""

    class _Capture:
        def __init__(self):
            self.frame = b""

        def write(self, frame):
            self.frame = bytes(frame)
            return len(frame)

    def __init__(self, *, source_system=1, source_component=1):
        from pymavlink import mavutil

        self.mavlink = mavutil.mavlink
        self.capture = self._Capture()
        self.mav = self.mavlink.MAVLink(
            self.capture,
            srcSystem=int(source_system),
            srcComponent=int(source_component),
        )
        self._lock = threading.Lock()

    def encode(self, **fields):
        message = self.mavlink.MAVLink_follow_target_message(
            int(fields["timestamp_ms"]),
            int(fields["capabilities"]),
            int(fields["lat"]),
            int(fields["lon"]),
            float(fields["alt"]),
            tuple(fields["vel"]),
            tuple(fields["acc"]),
            tuple(fields["attitude_q"]),
            tuple(fields["rates"]),
            tuple(fields["position_cov"]),
            int(fields["custom_state"]),
        )
        with self._lock:
            self.capture.frame = b""
            self.mav.send(message, force_mavlink1=False)
            frame = self.capture.frame
        if not frame or frame[0] != 0xFD:
            raise RuntimeError("FOLLOW_TARGET encoder did not produce MAVLink2")
        return frame


class FollowTargetPublisher:
    def __init__(
        self,
        *,
        encoder=None,
        clock=time.monotonic,
        rate_hz=10.0,
        max_age_s=1.5,
        source_system=1,
        source_component=1,
    ):
        self.clock = clock
        self.period_s = 1.0 / float(rate_hz)
        self.max_age_s = float(max_age_s)
        self.source_system = int(source_system)
        self.source_component = int(source_component)
        self.encoder = encoder or PymavlinkFollowTargetEncoder(
            source_system=source_system,
            source_component=source_component,
        )
        self._lock = threading.Lock()
        self._heartbeat_at = None
        self._position_at = None
        self._last_emit_at = None
        self._position = None
        self._attitude_q = (1.0, 0.0, 0.0, 0.0)
        self._rates = (0.0, 0.0, 0.0)
        self._produced = 0
        self._stale = 0
        self._dropped = 0

    def observe(self, message):
        if _source_system(message) != self.source_system:
            return
        kind = message_type(message)
        now = float(self.clock())
        with self._lock:
            if kind == "HEARTBEAT":
                self._heartbeat_at = now
            elif kind == "GLOBAL_POSITION_INT":
                self._position = {
                    "timestamp_ms": int(field(message, "time_boot_ms", now * 1000)),
                    "lat": int(field(message, "lat", 0) or 0),
                    "lon": int(field(message, "lon", 0) or 0),
                    "alt": float(field(message, "alt", 0) or 0) / 1000.0,
                    "vel": (
                        float(field(message, "vx", 0) or 0) / 100.0,
                        float(field(message, "vy", 0) or 0) / 100.0,
                        float(field(message, "vz", 0) or 0) / 100.0,
                    ),
                }
                self._position_at = now
            elif kind == "ATTITUDE":
                self._attitude_q = _euler_quaternion(
                    float(field(message, "roll", 0.0) or 0.0),
                    float(field(message, "pitch", 0.0) or 0.0),
                    float(field(message, "yaw", 0.0) or 0.0),
                )
                self._rates = (
                    float(field(message, "rollspeed", 0.0) or 0.0),
                    float(field(message, "pitchspeed", 0.0) or 0.0),
                    float(field(message, "yawspeed", 0.0) or 0.0),
                )

    def next_frame(self):
        now = float(self.clock())
        with self._lock:
            fresh = (
                self._position is not None
                and self._heartbeat_at is not None
                and self._position_at is not None
                and now - self._heartbeat_at <= self.max_age_s
                and now - self._position_at <= self.max_age_s
                and bool(self._position["lat"] and self._position["lon"])
            )
            if not fresh:
                self._stale += 1
                return None
            if (
                self._last_emit_at is not None
                and now - self._last_emit_at + 1e-9 < self.period_s
            ):
                return None
            payload = dict(self._position)
            payload.update({
                "message_id": FOLLOW_TARGET_MESSAGE_ID,
                "source_system": self.source_system,
                "source_component": self.source_component,
                "capabilities": FOLLOW_TARGET_CAPABILITIES,
                "acc": (0.0, 0.0, 0.0),
                "attitude_q": self._attitude_q,
                "rates": self._rates,
                "position_cov": (0.0, 0.0, 0.0),
                "custom_state": 0,
            })
            self._last_emit_at = now
        try:
            frame = bytes(self.encoder.encode(**payload))
        except Exception:
            with self._lock:
                self._dropped += 1
            raise
        with self._lock:
            self._produced += 1
        return frame

    def snapshot(self):
        with self._lock:
            return {
                "produced": self._produced,
                "stale": self._stale,
                "dropped": self._dropped,
                "heartbeat_age_s": None if self._heartbeat_at is None else max(0.0, float(self.clock()) - self._heartbeat_at),
                "position_age_s": None if self._position_at is None else max(0.0, float(self.clock()) - self._position_at),
            }
