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
            "updated_at",
            "lat",
            "lng",
            "ground_speed",
            "heading",
            "battery_percent",
            "flight_mode",
            "lte_rssi",
            "fc_link",
            "last_command",
            "aircraft_link",
            "aircraft_age",
        )
        messages = aircraft.get("messages")
        if isinstance(messages, list):
            valid_messages = [item for item in messages if isinstance(item, dict)]
            latest_heartbeat = next(
                (
                    item for item in reversed(valid_messages)
                    if str(item.get("type", "")).upper() == "HEARTBEAT"
                ),
                None,
            )
            for keep in (4, 3, 2, 1):
                selected_messages = valid_messages[-keep:]
                if latest_heartbeat and latest_heartbeat not in selected_messages:
                    selected_messages = (
                        [latest_heartbeat]
                        if keep == 1
                        else [latest_heartbeat, *selected_messages[-(keep - 1):]]
                    )
                compact_aircraft = {
                    "link_active": aircraft.get("link_active"),
                    "last_seen_age_sec": aircraft.get("last_seen_age_sec"),
                    "mode": data.get("aircraft_mode"),
                    "armed": data.get("aircraft_armed"),
                    "battery_percent": data.get("aircraft_battery_percent"),
                    "lat": data.get("aircraft_lat"),
                    "lng": data.get("aircraft_lng"),
                    "altitude": data.get("aircraft_altitude"),
                    "ground_speed": data.get("aircraft_ground_speed"),
                    "heading": data.get("aircraft_heading"),
                    "mission_status": data.get("aircraft_mission_status"),
                }
                compact_aircraft = {
                    key: value for key, value in compact_aircraft.items() if value is not None
                }
                compact_aircraft["messages"] = [
                    {
                        "time": item.get("time"),
                        "type": str(item.get("type", "MSG"))[:16],
                        "text": str(item.get("text", ""))[:120],
                    }
                    for item in selected_messages
                ]
                prioritized = {key: data[key] for key in curated_keys if key in data}
                prioritized["aircraft"] = compact_aircraft
                text = dump(prioritized)
                if len(text.encode("utf-8")) <= max_bytes:
                    return text

            heartbeat = (
                latest_heartbeat
                or next(
                    (item for item in reversed(valid_messages)),
                    None,
                )
            )
            essential_aircraft = {
                "link_active": aircraft.get("link_active"),
                "mode": data.get("aircraft_mode"),
                "armed": data.get("aircraft_armed"),
                "battery_percent": data.get("aircraft_battery_percent"),
                "lat": data.get("aircraft_lat"),
                "lng": data.get("aircraft_lng"),
                "altitude": data.get("aircraft_altitude"),
                "ground_speed": data.get("aircraft_ground_speed"),
                "heading": data.get("aircraft_heading"),
                "messages": (
                    [{
                        "time": heartbeat.get("time"),
                        "type": str(heartbeat.get("type", "MSG"))[:16],
                        "text": str(heartbeat.get("text", ""))[:96],
                    }]
                    if heartbeat
                    else []
                ),
            }
            for key in ("lat", "lng"):
                if isinstance(essential_aircraft.get(key), (int, float)):
                    essential_aircraft[key] = round(essential_aircraft[key], 7)
            for key in ("altitude", "ground_speed", "heading"):
                if isinstance(essential_aircraft.get(key), (int, float)):
                    essential_aircraft[key] = round(essential_aircraft[key], 2)
            essential_aircraft = {
                key: value
                for key, value in essential_aircraft.items()
                if value is not None
            }
            essential = {
                key: data[key]
                for key in (
                    "updated_at", "lat", "lng", "ground_speed", "heading",
                    "battery_percent", "armed", "flight_mode", "lte_rssi",
                    "last_command", "aircraft_link",
                )
                if key in data
            }
            essential["aircraft"] = essential_aircraft
            text = dump(essential)
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
        "updated_at",
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
        "aircraft_command_event",
        "aircraft_command_event_id",
        "aircraft_command_fault",
        "steering",
        "throttle",
        "last_command",
        "aircraft_link",
        "aircraft_age",
        "aircraft_packets",
        "aircraft_msg",
        "aircraft_msg_time",
        "aircraft_mode",
        "aircraft_armed",
        "aircraft_battery_percent",
        "aircraft_lat",
        "aircraft_lng",
        "aircraft_altitude",
        "aircraft_ground_speed",
        "aircraft_heading",
        "aircraft_mission_status",
    )
    fallback = {key: data[key] for key in curated_keys if key in data}
    if "aircraft_msg" in fallback:
        fallback["aircraft_msg"] = str(fallback["aircraft_msg"])[:48]
    text = dump(fallback)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    fallback["aircraft_msg"] = str(fallback.get("aircraft_msg", ""))[:24]
    text = dump(fallback)
    if len(text.encode("utf-8")) <= max_bytes:
        return text

    removable = (
        "aircraft_command_fault",
        "aircraft_command_event_id",
        "aircraft_msg_time",
        "aircraft_age",
        "aircraft_packets",
        "aircraft_msg",
        "aircraft_fault_text",
        "aircraft_mission_status",
        "aircraft_tx_id",
        "rover_tx_id",
        "satellites_visible",
        "gps_fix_type",
        "mission_status",
    )
    for key in removable:
        fallback.pop(key, None)
        text = dump(fallback)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

    # Core telemetry alone is comfortably below the product DP limit.
    core_keys = (
        "updated_at",
        "lat",
        "lng",
        "ground_speed",
        "heading",
        "battery_percent",
        "armed",
        "flight_mode",
        "lte_rssi",
        "fc_link",
        "steering",
        "throttle",
        "last_command",
        "aircraft_link",
        "aircraft_command_event",
    )
    return dump({key: fallback[key] for key in core_keys if key in fallback})


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
    connection_generation: int = 0
    gps_generation: int = 0
    position_generation: int = 0
    ekf_generation: int = 0
    home_generation: int = 0
    gps_updated_monotonic: float = 0.0
    position_updated_monotonic: float = 0.0
    ekf_updated_monotonic: float = 0.0
    home_updated_monotonic: float = 0.0
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
    aircraft_command_event: str = ""
    aircraft_command_event_id: str = ""
    aircraft_command_fault: str = ""
    updated_at: float = field(default_factory=time.time)

    def data(self) -> dict[str, Any]:
        data = {
            "updated_at": round(time.time(), 3),
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
        if self.aircraft_command_event or self.aircraft_command_fault:
            data.update(
                {
                    "aircraft_command_event": str(
                        self.aircraft_command_event
                    )[:24],
                    "aircraft_command_event_id": str(
                        self.aircraft_command_event_id
                    )[:36],
                    "aircraft_command_fault": str(
                        self.aircraft_command_fault
                    )[:120],
                }
            )
        return data

    def record_aircraft_command_event(
        self, command_id: str, event: str, fault: str = ""
    ) -> None:
        self.aircraft_command_event_id = str(command_id)[:36]
        self.aircraft_command_event = str(event)[:24]
        self.aircraft_command_fault = str(fault)[:120]

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


def record_command_receipt(
    telemetry: RoverTelemetry,
    target: str,
    command_id: str,
    accepted: bool,
    stage: str,
    message: str,
) -> None:
    if target == "aircraft":
        telemetry.record_aircraft_command_event(
            command_id,
            "accepted" if accepted else "rejected",
            "" if accepted else message,
        )
        return
    telemetry.mission_status = message if accepted else "command_failed"
    telemetry.fault_text = "" if accepted else message
    telemetry.rover_transaction_stage = stage
    telemetry.rover_transaction_id = command_id
    telemetry.rover_transaction_pending = 0


def apply_rover_mission_status(telemetry, status) -> dict[str, Any]:
    """Apply one terminal async mission result and return a typed receipt."""
    telemetry.rover_transaction_id = str(status.command_id)
    telemetry.rover_transaction_stage = str(status.stage)
    telemetry.rover_transaction_pending = 0
    telemetry.mission_status = str(status.message or status.stage)
    telemetry.fault_text = (
        f"{status.error_type}: {status.message}"
        if status.stage == "failed"
        else ""
    )
    return {
        "command_id": str(status.command_id),
        "stage": str(status.stage),
        "accepted": status.stage == "verified",
        "error_type": str(status.error_type),
        "message": str(status.message),
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
