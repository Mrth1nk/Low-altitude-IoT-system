from __future__ import annotations

import threading
import time


def field(message, name, default=0):
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def message_type(message):
    if isinstance(message, dict):
        return str(message.get("type", ""))
    getter = getattr(message, "get_type", None)
    return str(getter() if getter else "")


class AircraftTelemetry:
    def __init__(self, *, clock=time.monotonic, freshness=3.0):
        self.clock = clock
        self.freshness = float(freshness)
        self._lock = threading.Lock()
        self._state = {
            "mode": "UNKNOWN",
            "armed": False,
            "gps_fix": 0,
            "satellites": 0,
            "lat": 0,
            "lon": 0,
            "home_valid": False,
            "ekf_flags": 0,
            "gps_at": 0.0,
            "position_at": 0.0,
            "home_at": 0.0,
            "ekf_at": 0.0,
        }

    def update(self, message):
        kind = message_type(message)
        now = float(self.clock())
        with self._lock:
            if kind == "HEARTBEAT":
                self._state["armed"] = bool(int(field(message, "base_mode", 0)) & 128)
                self._state["mode"] = str(
                    field(message, "mode", field(message, "custom_mode", "UNKNOWN"))
                )
            elif kind == "GPS_RAW_INT":
                self._state["gps_fix"] = int(field(message, "fix_type", 0) or 0)
                self._state["satellites"] = int(
                    field(message, "satellites_visible", 0) or 0
                )
                self._state["gps_at"] = now
            elif kind == "GLOBAL_POSITION_INT":
                self._state["lat"] = int(field(message, "lat", 0) or 0)
                self._state["lon"] = int(field(message, "lon", 0) or 0)
                self._state["position_at"] = now
            elif kind == "HOME_POSITION":
                lat = int(field(message, "latitude", 0) or 0)
                lon = int(field(message, "longitude", 0) or 0)
                self._state["home_valid"] = bool(lat and lon)
                self._state["home_at"] = now
            elif kind == "EKF_STATUS_REPORT":
                self._state["ekf_flags"] = int(field(message, "flags", 0) or 0)
                self._state["ekf_at"] = now

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def execution_gate(self):
        state = self.snapshot()
        now = float(self.clock())
        checks = (
            (state["gps_fix"] >= 3, "GPS fix"),
            (state["satellites"] >= 6, "satellites"),
            (bool(state["lat"] and state["lon"]), "position"),
            (state["home_valid"], "Home"),
            ((state["ekf_flags"] & 0x11) == 0x11, "EKF"),
        )
        missing = [label for valid, label in checks if not valid]
        for label, timestamp in (
            ("GPS", state["gps_at"]),
            ("position", state["position_at"]),
            ("Home", state["home_at"]),
            ("EKF", state["ekf_at"]),
        ):
            if timestamp <= 0 or now - timestamp > self.freshness:
                missing.append(f"{label} stale")
        return not missing, "" if not missing else "missing " + ", ".join(missing)
