from __future__ import annotations

import math
import threading
import time

from aircraft_agent.telemetry import field, message_type


class SlaveStateAggregator:
    """Builds the typed slave status from the sole MAVLink reader stream."""

    def __init__(self, *, clock=time.time, heartbeat_freshness=3.0):
        self.clock = clock
        self.heartbeat_freshness = float(heartbeat_freshness)
        self._lock = threading.Lock()
        self._state = {
            "heartbeat_at": 0.0,
            "mode": "UNKNOWN",
            "armed": False,
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
            "event": {"timestamp": 0.0, "sequence": 0, "type": "NONE", "text": ""},
        }

    def update(self, message):
        kind = message_type(message)
        now = float(self.clock())
        with self._lock:
            if kind == "HEARTBEAT":
                self._state["heartbeat_at"] = now
                self._state["armed"] = bool(int(field(message, "base_mode", 0) or 0) & 128)
                self._state["mode"] = self._mode(message)
            elif kind == "GLOBAL_POSITION_INT":
                lat = int(field(message, "lat", 0) or 0)
                lon = int(field(message, "lon", 0) or 0)
                self._state["lat"] = lat / 1e7
                self._state["lon"] = lon / 1e7
                self._state["position_observed"] = bool(lat or lon)
                self._state["altitude"] = float(field(message, "relative_alt", 0) or 0) / 1000.0
                vx = float(field(message, "vx", 0) or 0) / 100.0
                vy = float(field(message, "vy", 0) or 0) / 100.0
                self._state["speed"] = math.hypot(vx, vy)
                heading = int(field(message, "hdg", 0) or 0)
                if heading != 0xFFFF:
                    self._state["heading"] = heading / 100.0
            elif kind == "BATTERY_STATUS":
                remaining = float(field(message, "battery_remaining", -1) or 0)
                self._state["battery"] = min(100.0, max(-1.0, remaining))

    def safe_update(self, message):
        try:
            self.update(message)
        except Exception as exc:
            self.set_fault(f"telemetry_update: {type(exc).__name__}: {exc}")

    def record_event(self, event_type, text, *, sequence=0, mission_id="", stage=None):
        now = float(self.clock())
        with self._lock:
            if stage is not None:
                self._state["mission_stage"] = str(stage)
            if mission_id:
                self._state["mission_id"] = str(mission_id)
            self._state["event"] = {
                "timestamp": now,
                "sequence": int(sequence) & 0xFFFFFFFF,
                "type": str(event_type),
                "text": str(text)[:160],
            }

    def set_fault(self, fault):
        with self._lock:
            self._state["fault"] = str(fault)[:160]

    def snapshot(self):
        now = float(self.clock())
        with self._lock:
            state = dict(self._state)
            state["event"] = dict(self._state["event"])
        connected = state["heartbeat_at"] > 0 and now - state["heartbeat_at"] <= self.heartbeat_freshness
        return {
            "online": connected,
            "fc_connected": connected,
            "blocked": False,
            "mode": state["mode"],
            "armed": state["armed"],
            "heartbeat_at": state["heartbeat_at"],
            "lat": state["lat"],
            "lon": state["lon"],
            "position_observed": state["position_observed"],
            "altitude": state["altitude"],
            "speed": state["speed"],
            "heading": state["heading"],
            "battery": state["battery"],
            "mission_stage": state["mission_stage"],
            "mission_id": state["mission_id"],
            "fault": state["fault"],
            "link_state": "ONLINE" if connected else "OFFLINE",
            "event": state["event"],
        }

    @staticmethod
    def _mode(message):
        mode = field(message, "mode", None)
        if isinstance(mode, str) and mode:
            return mode.upper()
        try:
            from pymavlink import mavutil

            decoded = mavutil.mode_string_v10(message)
            if decoded:
                return str(decoded).upper()
        except Exception:
            pass
        return str(field(message, "custom_mode", "UNKNOWN"))
