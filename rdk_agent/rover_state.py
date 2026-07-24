from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any
import json


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compact_json_bytes(data: dict[str, Any], max_bytes: int = 480) -> str:
    def dump(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    text = dump(data)
    if len(text.encode("utf-8")) <= max_bytes:
        return text

    aircraft = data.get("aircraft")
    if isinstance(aircraft, dict):
        curated_keys = (
            "lat",
            "lng",
            "ground_speed",
            "heading",
            "battery_percent",
            "flight_mode",
            "lte_rssi",
            "fc_link",
            "gps_fix_type",
            "satellites_visible",
            "mission_status",
            "rover_tx_stage",
            "rover_tx_id",
            "rover_tx_pending",
            "aircraft_tx_stage",
            "aircraft_tx_id",
            "aircraft_tx_pending",
            "aircraft_mission_status",
            "aircraft_fault_text",
            "last_command",
            "aircraft_link",
            "aircraft_age",
            "aircraft_packets",
            "aircraft_msg",
            "aircraft_msg_time",
        )
        messages = aircraft.get("messages")
        if isinstance(messages, list):
            for keep in (4, 3, 2, 1):
                compact_aircraft = {
                    "link_active": aircraft.get("link_active"),
                    "last_seen_age_sec": aircraft.get("last_seen_age_sec"),
                    "packets_received": aircraft.get("packets_received"),
                    "messages": [
                        {
                            "time": item.get("time"),
                            "type": str(item.get("type", "MSG"))[:16],
                            "text": str(item.get("text", ""))[:120],
                        }
                        for item in messages[-keep:]
                        if isinstance(item, dict)
                    ],
                }
                prioritized = {key: data[key] for key in curated_keys if key in data}
                prioritized["aircraft"] = compact_aircraft
                text = dump(prioritized)
                if len(text.encode("utf-8")) <= max_bytes:
                    return text

        smaller = dict(data)
        compact_aircraft = dict(aircraft)
        messages = compact_aircraft.get("messages")
        if isinstance(messages, list):
            compact_aircraft["messages"] = [
                {
                    "time": item.get("time"),
                    "type": str(item.get("type", "MSG"))[:16],
                    "text": str(item.get("text", ""))[:160],
                }
                for item in messages[-10:]
                if isinstance(item, dict)
            ]
        smaller["aircraft"] = compact_aircraft
        text = dump(smaller)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

        if isinstance(messages, list):
            compact_aircraft["messages"] = [
                {
                    "time": item.get("time"),
                    "type": str(item.get("type", "MSG"))[:16],
                    "text": str(item.get("text", ""))[:120],
                }
                for item in messages[-6:]
                if isinstance(item, dict)
            ]
        smaller["aircraft"] = compact_aircraft
        text = dump(smaller)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

        compact_aircraft.pop("messages", None)
        smaller["aircraft"] = compact_aircraft
        text = dump(smaller)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

        smaller.pop("aircraft", None)
        text = dump(smaller)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

    fallback = dict(data)
    curated_keys = (
        "lat",
        "lng",
        "altitude",
        "ground_speed",
        "heading",
        "battery_percent",
        "armed",
        "flight_mode",
        "lte_rssi",
        "fc_link",
        "gps_fix_type",
        "satellites_visible",
        "mission_status",
        "rover_tx_stage",
        "rover_tx_id",
        "rover_tx_pending",
        "aircraft_tx_stage",
        "aircraft_tx_id",
        "aircraft_tx_pending",
        "aircraft_mission_status",
        "aircraft_fault_text",
        "steering",
        "throttle",
        "last_command",
        "aircraft_link",
        "aircraft_age",
        "aircraft_packets",
        "aircraft_msg",
        "aircraft_msg_time",
    )
    fallback = {key: data[key] for key in curated_keys if key in data}
    if "aircraft_msg" in fallback:
        fallback["aircraft_msg"] = str(fallback["aircraft_msg"])[:48]
    text = dump(fallback)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    fallback["aircraft_msg"] = str(fallback.get("aircraft_msg", ""))[:24]
    return dump(fallback)


@dataclass
class RoverTelemetry:
    lat: float = 32.119740
    lng: float = 118.953140
    altitude: float = 0.0
    ground_speed: float = 0.0
    heading: int = 0
    battery_percent: int = 100
    armed: bool = False
    flight_mode: str = "standby"
    lte_rssi: int = 0
    fc_link: bool = False
    gps_fix_type: int = 0
    satellites_visible: int = 0
    ekf_flags: int = 0
    control_mode: str = "standby"
    mission_status: str = "idle"
    target_lat: float = 0.0
    target_lng: float = 0.0
    target_speed: float = 0.5
    steering: int = 0
    throttle: int = 0
    last_command: str = "none"
    fault_text: str = ""
    rover_transaction_stage: str = "idle"
    rover_transaction_id: str = ""
    rover_transaction_pending: int = 0
    aircraft_transaction_stage: str = "idle"
    aircraft_transaction_id: str = ""
    aircraft_transaction_pending: int = 0
    aircraft_transaction_revision: str = ""
    aircraft_mission_status: str = "idle"
    aircraft_fault_text: str = ""
    updated_at: float = field(default_factory=time.time)

    def data(self) -> dict[str, Any]:
        data = {
            "lat": round(float(self.lat), 7),
            "lng": round(float(self.lng), 7),
            "altitude": round(float(self.altitude), 2),
            "ground_speed": round(float(self.ground_speed), 2),
            "heading": int(self.heading) % 360,
            "battery_percent": 100,
            "armed": bool(self.armed),
            "flight_mode": str(self.flight_mode)[:64],
            "lte_rssi": int(self.lte_rssi),
            "fc_link": bool(self.fc_link),
            "gps_fix_type": int(self.gps_fix_type),
            "satellites_visible": int(self.satellites_visible),
            "ekf_flags": int(self.ekf_flags),
            "waypoint_ready": bool(self.gps_fix_type >= 3 and self.satellites_visible >= 6 and abs(self.lat) > 0.000001 and abs(self.lng) > 0.000001),
            "control_mode": str(self.control_mode)[:32],
            "mission_status": str(self.mission_status)[:64],
            "target_lat": round(float(self.target_lat), 7),
            "target_lng": round(float(self.target_lng), 7),
            "target_speed": round(float(clamp(self.target_speed, 0.0, 3.0)), 2),
            "steering": int(clamp(self.steering, -100, 100)),
            "throttle": int(clamp(self.throttle, -100, 100)),
            "last_command": str(self.last_command)[:64],
            "fault_text": str(self.fault_text)[:255],
        }
        if (
            self.rover_transaction_stage != "idle"
            or self.rover_transaction_id
            or self.rover_transaction_pending
        ):
            data.update(
                {
                    "rover_tx_stage": str(self.rover_transaction_stage)[:24],
                    "rover_tx_id": str(self.rover_transaction_id)[:36],
                    "rover_tx_pending": int(
                        clamp(self.rover_transaction_pending, 0, 999)
                    ),
                }
            )
        if (
            self.aircraft_transaction_stage != "idle"
            or self.aircraft_transaction_id
            or self.aircraft_transaction_pending
        ):
            data.update(
                {
                    "aircraft_tx_stage": str(
                        self.aircraft_transaction_stage
                    )[:24],
                    "aircraft_tx_id": str(self.aircraft_transaction_id)[:36],
                    "aircraft_tx_pending": int(
                        clamp(self.aircraft_transaction_pending, 0, 999)
                    ),
                    "aircraft_mission_status": str(
                        self.aircraft_mission_status
                    )[:48],
                    "aircraft_fault_text": str(self.aircraft_fault_text)[:120],
                }
            )
        return data

    def apply_aircraft_transaction(self, state: dict[str, Any]) -> bool:
        revision = str(state.get("revision", ""))
        if revision == self.aircraft_transaction_revision:
            return False
        self.aircraft_transaction_revision = revision
        self.aircraft_transaction_stage = str(state.get("stage", "idle"))
        self.aircraft_transaction_id = str(state.get("transaction_id", ""))
        self.aircraft_transaction_pending = int(state.get("pending", 0) or 0)
        self.aircraft_mission_status = self.aircraft_transaction_stage
        self.aircraft_fault_text = str(state.get("error", ""))[:120]
        return True

    def tuya_report_payload(self) -> dict:
        now_ms = int(time.time() * 1000)
        return {
            "msgId": uuid.uuid4().hex,
            "time": now_ms,
            "sys": {"ack": 1},
            "data": {k: {"value": v, "time": now_ms} for k, v in self.data().items()},
        }

    def tuya_compact_payload(self, extra_state: dict[str, Any] | None = None) -> dict:
        now_ms = int(time.time() * 1000)
        data = self.data()
        if extra_state:
            data.update(extra_state)
        cloud_data = {
            "rover_state": compact_json_bytes(data),
            "command": str(data["last_command"]),
            "target_lat": str(data["target_lat"]),
            "target_lng": str(data["target_lng"]),
            "target_speed": str(data["target_speed"]),
            "steering": str(data["steering"]),
            "throttle": str(data["throttle"]),
        }
        return {
            "msgId": uuid.uuid4().hex,
            "time": now_ms,
            "sys": {"ack": 1},
            "data": {k: {"value": v, "time": now_ms} for k, v in cloud_data.items()},
        }


@dataclass
class RoverCommand:
    command: str = "noop"
    target_lat: float = 0.0
    target_lng: float = 0.0
    target_speed: float = 0.5
    steering: int = 0
    throttle: int = 0
    control_mode: str = "standby"
    source: str = "local"
    received_at: float = field(default_factory=time.time)

    @classmethod
    def from_tuya_data(cls, data: dict, source: str = "tuya") -> "RoverCommand":
        flattened = {}
        for key, value in data.items():
            if isinstance(value, dict) and "value" in value:
                flattened[key] = value["value"]
            else:
                flattened[key] = value
        return cls.from_dict(flattened, source=source)

    @classmethod
    def from_dict(cls, data: dict, source: str = "local") -> "RoverCommand":
        command = str(data.get("command", data.get("last_command", "noop")) or "noop").lower()
        raw_target_speed = float(data.get("target_speed", 0.5) or 0.5)
        target_speed = (
            float(clamp(raw_target_speed, 1.0, 120.0))
            if command.startswith("aircraft_")
            else float(clamp(raw_target_speed, 0.0, 3.0))
        )
        return cls(
            command=command,
            target_lat=float(data.get("target_lat", 0.0) or 0.0),
            target_lng=float(data.get("target_lng", 0.0) or 0.0),
            target_speed=target_speed,
            steering=int(clamp(float(data.get("steering", 0) or 0), -100, 100)),
            throttle=int(clamp(float(data.get("throttle", 0) or 0), -100, 100)),
            control_mode=str(data.get("control_mode", "standby") or "standby").lower(),
            source=source,
        )
