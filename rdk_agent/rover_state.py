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

    if str(data.get("last_command", "")).lower() == "auto":
        source_aircraft = (
            data.get("aircraft")
            if isinstance(data.get("aircraft"), dict)
            else {}
        )
        auto_aircraft = {
            "link_active": source_aircraft.get(
                "link_active", data.get("aircraft_link")
            ),
            "mode": data.get("aircraft_mode", source_aircraft.get("mode")),
            "armed": data.get("aircraft_armed", source_aircraft.get("armed")),
            "position_observed": data.get(
                "aircraft_position_observed",
                source_aircraft.get("position_observed", False),
            ),
            "lat": data.get("aircraft_lat", source_aircraft.get("lat")),
            "lng": data.get("aircraft_lng", source_aircraft.get("lng")),
            "altitude": data.get(
                "aircraft_altitude", source_aircraft.get("altitude")
            ),
            "heading": data.get(
                "aircraft_heading", source_aircraft.get("heading")
            ),
        }
        for key in ("lat", "lng"):
            if isinstance(auto_aircraft.get(key), (int, float)):
                auto_aircraft[key] = round(float(auto_aircraft[key]), 5)
        for key in ("altitude", "heading"):
            if isinstance(auto_aircraft.get(key), (int, float)):
                auto_aircraft[key] = round(float(auto_aircraft[key]), 2)
        auto_aircraft = {
            key: value
            for key, value in auto_aircraft.items()
            if value is not None
        }
        auto_state = {
            "updated_at": data.get("updated_at"),
            "lat": round(float(data.get("lat", 0.0)), 5),
            "lng": round(float(data.get("lng", 0.0)), 5),
            "position_observed": bool(data.get("position_observed", False)),
            "flight_mode": data.get("flight_mode"),
            "armed": data.get("armed"),
            "fc_link": data.get("fc_link"),
            "gps_fix_type": data.get("gps_fix_type"),
            "satellites_visible": data.get("satellites_visible"),
            "rover_tx_stage": data.get("rover_tx_stage"),
            "mission_status": str(data.get("mission_status", ""))[:48],
            "fault_text": str(data.get("fault_text", ""))[:96],
            "last_command": "auto",
            "aircraft_link": data.get("aircraft_link"),
            "aircraft": auto_aircraft,
        }
        auto_state = {
            key: value
            for key, value in auto_state.items()
            if value not in (None, "")
        }
        for key in ("mission_status", "fc_link"):
            text = dump(auto_state)
            if len(text.encode("utf-8")) <= max_bytes:
                return text
            auto_state.pop(key, None)
        for key in ("altitude", "heading"):
            text = dump(auto_state)
            if len(text.encode("utf-8")) <= max_bytes:
                return text
            auto_aircraft.pop(key, None)
        if "fault_text" in auto_state:
            auto_state["fault_text"] = auto_state["fault_text"][:64]
        text = dump(auto_state)
        if len(text.encode("utf-8")) <= max_bytes:
            return text

    aircraft = data.get("aircraft")
    if isinstance(aircraft, dict):
        curated_keys = (
            "updated_at",
            "lat",
            "lng",
            "position_observed",
            "ground_speed",
            "heading",
            "battery_percent",
            "flight_mode",
            "lte_rssi",
            "fc_link",
            "last_command",
            "aircraft_link",
            "aircraft_age",
            "aircraft_tx_stage",
            "aircraft_tx_pending",
            "aircraft_mission_status",
            "aircraft_fault_text",
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
                    "position_observed": data.get(
                        "aircraft_position_observed",
                        aircraft.get("position_observed"),
                    ),
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
                prioritized = {
                    key: data[key]
                    for key in curated_keys
                    if key in data
                    and (key != "position_observed" or data[key] is True)
                }
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
            aircraft_stage = str(data.get("aircraft_tx_stage", "") or "")
            if aircraft_stage.lower() not in ("", "idle"):
                mission_aircraft = {
                    "link_active": aircraft.get("link_active"),
                    "mode": data.get("aircraft_mode"),
                    "armed": data.get("aircraft_armed"),
                    "lat": data.get("aircraft_lat", aircraft.get("lat")),
                    "lng": data.get("aircraft_lng", aircraft.get("lng")),
                    "position_observed": data.get(
                        "aircraft_position_observed",
                        aircraft.get("position_observed"),
                    ),
                    "altitude": data.get("aircraft_altitude"),
                    "ground_speed": data.get("aircraft_ground_speed"),
                    "heading": data.get("aircraft_heading"),
                    "messages": (
                        [{
                            "time": heartbeat.get("time"),
                            "type": str(heartbeat.get("type", "MSG"))[:16],
                            "text": str(heartbeat.get("text", ""))[:72],
                        }]
                        if heartbeat
                        else []
                    ),
                }
                mission_aircraft = {
                    key: value
                    for key, value in mission_aircraft.items()
                    if value is not None
                }
                for key in ("lat", "lng"):
                    if isinstance(mission_aircraft.get(key), (int, float)):
                        mission_aircraft[key] = round(mission_aircraft[key], 7)
                mission_state = {
                    key: data[key]
                    for key in (
                        "updated_at", "lat", "lng", "flight_mode", "lte_rssi",
                        "last_command", "aircraft_link",
                        "aircraft_tx_stage", "aircraft_tx_pending",
                        "aircraft_mission_status", "aircraft_fault_text",
                    )
                    if key in data and data[key] not in (None, "")
                }
                mission_state["aircraft"] = mission_aircraft
                text = dump(mission_state)
                if len(text.encode("utf-8")) <= max_bytes:
                    return text

            essential_aircraft = {
                "link_active": aircraft.get("link_active"),
                "mode": data.get("aircraft_mode"),
                "armed": data.get("aircraft_armed"),
                "battery_percent": data.get("aircraft_battery_percent"),
                "lat": data.get("aircraft_lat"),
                "lng": data.get("aircraft_lng"),
                "position_observed": data.get(
                    "aircraft_position_observed",
                    aircraft.get("position_observed"),
                ),
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
                    "position_observed",
                    "battery_percent", "armed", "flight_mode", "lte_rssi",
                    "last_command", "aircraft_link",
                )
                if key in data
                and (key != "position_observed" or data[key] is True)
            }
            essential["aircraft"] = essential_aircraft
            text = dump(essential)
            if len(text.encode("utf-8")) <= max_bytes:
                return text

            # A command receipt plus two observed 0,0 positions can push the
            # otherwise useful state just over Tuya's 480-byte DP limit. Keep
            # one real heartbeat and the aircraft status before expendable
            # Rover controls so a successful command cannot blank telemetry.
            live_aircraft = {
                "link_active": aircraft.get("link_active"),
                "mode": data.get("aircraft_mode"),
                "armed": data.get("aircraft_armed"),
                "battery_percent": data.get("aircraft_battery_percent"),
                "lat": data.get("aircraft_lat", aircraft.get("lat")),
                "lng": data.get("aircraft_lng", aircraft.get("lng")),
                "position_observed": data.get(
                    "aircraft_position_observed",
                    aircraft.get("position_observed"),
                ),
                "altitude": data.get("aircraft_altitude"),
                "ground_speed": data.get("aircraft_ground_speed"),
                "heading": data.get("aircraft_heading"),
                "messages": (
                    [{
                        "time": heartbeat.get("time"),
                        "type": str(heartbeat.get("type", "HEARTBEAT"))[:16],
                        "text": str(heartbeat.get("text", ""))[:64],
                    }]
                    if heartbeat
                    else []
                ),
            }
            for key in ("lat", "lng"):
                if isinstance(live_aircraft.get(key), (int, float)):
                    live_aircraft[key] = round(float(live_aircraft[key]), 5)
            for key in ("altitude", "ground_speed", "heading"):
                if isinstance(live_aircraft.get(key), (int, float)):
                    live_aircraft[key] = round(float(live_aircraft[key]), 2)
            live_aircraft = {
                key: value
                for key, value in live_aircraft.items()
                if value is not None
            }
            live_state = {
                "updated_at": data.get("updated_at"),
                "lat": round(float(data.get("lat", 0.0)), 5),
                "lng": round(float(data.get("lng", 0.0)), 5),
                "position_observed": bool(data.get("position_observed", False)),
                "flight_mode": data.get("flight_mode"),
                "last_command": data.get("last_command"),
                "aircraft_link": data.get("aircraft_link"),
                "aircraft_command_event": data.get("aircraft_command_event"),
                "aircraft": live_aircraft,
            }
            live_state = {
                key: value
                for key, value in live_state.items()
                if value not in (None, "")
            }
            for container, key in (
                (live_state, "aircraft_command_event"),
                (live_aircraft, "ground_speed"),
                (live_aircraft, "battery_percent"),
                (live_state, "flight_mode"),
            ):
                text = dump(live_state)
                if len(text.encode("utf-8")) <= max_bytes:
                    return text
                container.pop(key, None)
            text = dump(live_state)
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
        "position_observed",
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
        "aircraft_position_observed",
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
        "position_observed",
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


def compact_slave_state(data: dict[str, Any], max_bytes: int = 480) -> str:
    """Serialize aircraft_2 independently without consuming rover_state bytes."""
    if not isinstance(data, dict):
        data = {}
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    compact = {
        "updated_at": round(float(data.get("updated_at", 0) or 0), 3),
        "online": bool(data.get("online", False)),
        "fc_connected": bool(data.get("fc_connected", False)),
        "blocked": bool(data.get("blocked", False)),
        "link_state": str(data.get("link_state", "OFFLINE"))[:24],
        "mode": str(data.get("mode", "UNKNOWN"))[:32],
        "armed": bool(data.get("armed", False)),
        "position_observed": bool(data.get("position_observed", False)),
        "lat": round(float(data.get("lat", 0) or 0), 6),
        "lon": round(float(data.get("lon", 0) or 0), 6),
        "altitude": round(float(data.get("altitude", 0) or 0), 2),
        "speed": round(float(data.get("speed", 0) or 0), 2),
        "heading": round(float(data.get("heading", 0) or 0), 2),
        "battery": round(float(data.get("battery", -1) or 0), 1),
        "mission_stage": str(data.get("mission_stage", "IDLE"))[:24],
        "mission_id": str(data.get("mission_id", ""))[:36],
        "fault": str(data.get("fault", ""))[:96],
        "event": {
            "timestamp": round(float(event.get("timestamp", 0) or 0), 3),
            "sequence": int(event.get("sequence", 0) or 0),
            "type": str(event.get("type", "NONE"))[:16],
            "text": str(event.get("text", ""))[:96],
        },
    }
    dump = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = dump(compact)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    for limit in (64, 40, 24, 0):
        compact["event"]["text"] = compact["event"]["text"][:limit]
        compact["fault"] = compact["fault"][:limit]
        text = dump(compact)
        if len(text.encode("utf-8")) <= max_bytes:
            return text
    for key in ("battery", "speed", "heading", "mission_id", "fault", "event"):
        compact.pop(key, None)
        text = dump(compact)
        if len(text.encode("utf-8")) <= max_bytes:
            return text
    return dump({
        "updated_at": compact["updated_at"],
        "online": compact["online"],
        "link_state": compact["link_state"],
        "mode": compact["mode"],
        "armed": compact["armed"],
    })


@dataclass
class RoverTelemetry:
    lat: float = 32.119740
    lng: float = 118.953140
    position_observed: bool = False
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
            "position_observed": bool(self.position_observed),
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

    def tuya_compact_payload(
        self,
        extra_state: dict[str, Any] | None = None,
        slave_state: dict[str, Any] | None = None,
    ) -> dict:
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
        if slave_state is not None:
            cloud_data["slave_state"] = compact_slave_state(slave_state)
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
    if target in ("aircraft", "aircraft_1"):
        telemetry.record_aircraft_command_event(
            command_id,
            "accepted" if accepted else "rejected",
            "" if accepted else message,
        )
        return
    if target == "aircraft_2":
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
