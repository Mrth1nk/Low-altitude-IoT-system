from __future__ import annotations

import math
import threading
import time

from aircraft_agent.telemetry import field, message_type


class SlaveStateAggregator:
    """Builds the typed slave status from the sole MAVLink reader stream."""

    def __init__(self, *, clock=time.time, heartbeat_freshness=3.0, optical_gate=None):
        self.clock = clock
        self.heartbeat_freshness = float(heartbeat_freshness)
        self.optical_gate = optical_gate
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._heartbeat_token = 0
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
                self._heartbeat_token += 1
                self._state["heartbeat_at"] = now
                self._state["armed"] = bool(int(field(message, "base_mode", 0) or 0) & 128)
                self._state["mode"] = self._mode(message)
                self._condition.notify_all()
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

    def heartbeat_token(self):
        with self._lock:
            return self._heartbeat_token

    def wait_for_heartbeat(self, after_token, predicate, *, timeout):
        deadline = time.monotonic() + float(timeout)
        with self._condition:
            while True:
                if self._heartbeat_token > int(after_token):
                    snapshot = self._snapshot_locked()
                    if predicate(snapshot):
                        return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)

    def snapshot(self):
        now = float(self.clock())
        with self._lock:
            state = self._snapshot_locked()
        connected = state["heartbeat_at"] > 0 and now - state["heartbeat_at"] <= self.heartbeat_freshness
        gate = (
            {"blocked": False, "state": "CLEAR", "reason": ""}
            if self.optical_gate is None
            else self.optical_gate.snapshot()
        )
        blocked = bool(gate.get("blocked", False))
        return {
            "online": connected,
            "fc_connected": connected,
            "blocked": blocked,
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
            "link_state": (
                "OPTICAL_BLOCKED"
                if blocked
                else ("ONLINE" if connected else "OFFLINE")
            ),
            "event": state["event"],
        }

    def record_stage(self, entry):
        stage = str(entry.get("stage", ""))
        mission_id = str(entry.get("mission_id", ""))
        self.record_event(
            "MISSION" if mission_id else "COMMAND",
            stage,
            sequence=int(entry.get("sequence", 0)),
            mission_id=mission_id,
            stage=stage,
        )

    def _snapshot_locked(self):
        state = dict(self._state)
        state["event"] = dict(self._state["event"])
        return state

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
