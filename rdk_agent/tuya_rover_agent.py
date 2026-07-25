#!/usr/bin/env python3
import argparse
import json
import os
import re
import ssl
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from aircraft_gateway import start_aircraft_gateway
from aircraft_link import AircraftLink
from aircraft_transport import AircraftTransport, legacy_gateway_ports
from command_router import CloudCommand, CommandRejected, CommandRouter
from l610 import check_l610, ensure_l610_usbnet, read_lte_rssi
from mavlink_rover import RoverMavlink, discover_mavlink_urls
from network_mode import NetworkModeExecutor
from rover_state import (
    RoverCommand,
    RoverTelemetry,
    apply_rover_mission_status,
    record_command_receipt,
)
from rover_mission import MissionError
from tuya_auth import build_tuya_credentials, make_topic
from aircraft_agent.state_store import AtomicJsonStore

CONFIG_PATH = Path.home() / "uav_tuya_agent" / "config.json"
STATE_PATH = Path.home() / "uav_tuya_agent" / "runtime_state.json"
COMMAND_PATH = Path.home() / "uav_tuya_agent" / "command_inbox.jsonl"
AIRCRAFT_STATE_PATH = Path.home() / "uav_tuya_agent" / "aircraft_state.json"
DEFAULT_HOST = "m1.tuyacn.com"
DEFAULT_PORT = 8883


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    data = json.loads(path.read_text())
    for key in ("device_id", "device_secret"):
        if not data.get(key) or str(data[key]).startswith("REPLACE_"):
            raise SystemExit(f"missing config value: {key} in {path}")
    data.setdefault("host", DEFAULT_HOST)
    data.setdefault("port", DEFAULT_PORT)
    data.setdefault("report_interval_sec", 2)
    data.setdefault("mavlink_url", "/dev/ttyACM0")
    data.setdefault("mavlink_baud", 115200)
    data.setdefault("at_port", "/dev/ttyUSB0")
    data.setdefault("state_path", str(STATE_PATH))
    data.setdefault("command_path", str(COMMAND_PATH))
    data.setdefault(
        "network_mode_request_path",
        "/run/low-altitude-iot/network-mode",
    )
    data.setdefault(
        "aircraft_auth_state_path",
        str(Path.home() / "uav_tuya_agent" / "aircraft_auth_state.json"),
    )
    return data


def mqtt_client(config: dict):
    creds = build_tuya_credentials(config["device_id"], config["device_secret"])
    client = mqtt.Client(client_id="tuyalink_" + creds["client_id"], protocol=mqtt.MQTTv311)
    client.username_pw_set(creds["username"], creds["password"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
    client.enable_logger()
    return client


def extract_property_set(payload: bytes) -> dict:
    try:
        doc = json.loads(payload.decode())
    except Exception:
        return {}
    data = doc.get("data", doc.get("payload", {}).get("data", {}))
    if not isinstance(data, dict):
        return {}
    result = dict(data)
    if "source_timestamp" not in result and doc.get("time") is not None:
        result["source_timestamp"] = doc["time"]
    return result


def append_local_command(command_path: Path, command: RoverCommand) -> None:
    command_path.parent.mkdir(parents=True, exist_ok=True)
    with command_path.open("a") as f:
        f.write(json.dumps(command.__dict__, separators=(",", ":")) + "\n")


def consume_local_commands(command_path: Path) -> list[CloudCommand]:
    if not command_path.exists():
        return []
    lines = command_path.read_text().splitlines()
    command_path.write_text("")
    commands = []
    for line in lines:
        if not line.strip():
            continue
        try:
            commands.append(
                CloudCommand.from_cloud(json.loads(line), source="ground_station")
            )
        except Exception:
            continue
    return commands


def write_state(path: Path, telemetry: RoverTelemetry, connected: bool) -> None:
    doc = {
        "online": connected,
        "updated_at": time.time(),
        "telemetry": telemetry.data(),
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2))


def read_aircraft_summary(path: Path = AIRCRAFT_STATE_PATH, transport_status=None) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"aircraft": {"link_active": False, "messages": []}}
    messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
    compact_messages = [
        {
            "time": round(float(item.get("time", 0) or 0), 2),
            "type": str(item.get("type", "MSG"))[:24],
            "text": str(item.get("text", ""))[:160],
            **{
                key: item[key]
                for key in (
                    "battery_percent", "lat", "lng", "altitude",
                    "ground_speed", "heading", "seq",
                )
                if key in item
            },
        }
        for item in messages[-24:]
        if isinstance(item, dict)
    ]
    updated_at = float(doc.get("updated_at", 0) or 0)
    age = time.time() - updated_at if updated_at else None
    heartbeat = next(
        (
            item for item in reversed(compact_messages)
            if str(item.get("type", "")).upper() == "HEARTBEAT"
        ),
        doc.get("latest_heartbeat")
        if isinstance(doc.get("latest_heartbeat"), dict)
        else None,
    )
    heartbeat_time = float(heartbeat.get("time", 0) or 0) if heartbeat else 0.0
    heartbeat_age = time.time() - heartbeat_time if heartbeat_time else None
    # The serial Wi-Fi module can deliver many position/raw packets between
    # heartbeats. Packet freshness proves the uplink is alive; the retained
    # heartbeat supplies stable mode/armed state without flickering.
    link_active = bool(age is not None and age < 3 and heartbeat is not None)
    last_message = ""
    last_message_time = None
    aircraft_mode = "UNKNOWN"
    aircraft_armed = None
    latest_by_type = {
        kind: next(
            (
                item for item in reversed(compact_messages)
                if str(item.get("type", "")).upper() == kind
            ),
            {},
        )
        for kind in ("SYS_STATUS", "GLOBAL_POSITION_INT", "VFR_HUD", "MISSION_CURRENT")
    }
    if heartbeat:
        item = heartbeat
        item_type = str(item.get("type", "MSG")).upper()
        item_text = str(item.get("text", ""))
        if item_type == "HEARTBEAT":
            mode_match = re.search(
                r"(?:心跳|HEARTBEAT)\s+([A-Z_]+)",
                item_text,
                re.IGNORECASE,
            )
            armed_match = re.search(
                r"armed\s*=\s*(YES|NO|TRUE|FALSE|0|1)",
                item_text,
                re.IGNORECASE,
            )
            mode = mode_match.group(1).upper() if mode_match else "UNKNOWN"
            armed = armed_match.group(1).upper() if armed_match else "UNKNOWN"
            if armed in ("TRUE", "1"):
                armed = "YES"
            elif armed in ("FALSE", "0"):
                armed = "NO"
            aircraft_mode = mode
            aircraft_armed = armed == "YES" if armed in ("YES", "NO") else None
            last_message = f"HEARTBEAT {mode} armed={armed}"
        last_message_time = item.get("time")
    system_status = latest_by_type["SYS_STATUS"]
    position = latest_by_type["GLOBAL_POSITION_INT"]
    hud = latest_by_type["VFR_HUD"]
    mission = latest_by_type["MISSION_CURRENT"]
    aircraft_details = {
        "battery_percent": system_status.get("battery_percent"),
        "lat": position.get("lat"),
        "lng": position.get("lng"),
        "altitude": position.get("altitude"),
        "ground_speed": hud.get("ground_speed", position.get("ground_speed")),
        "heading": hud.get("heading", position.get("heading")),
        "mission_status": (
            f"seq={mission['seq']}" if "seq" in mission else None
        ),
    }
    return {
        "aircraft": {
            "link_active": link_active,
            "last_seen_age_sec": (
                round(heartbeat_age, 2) if heartbeat_age is not None else None
            ),
            "last_remote": str(doc.get("last_remote", ""))[:64],
            "packets_received": int(doc.get("packets_received", 0) or 0),
            "bytes_received": int(doc.get("bytes_received", 0) or 0),
            "messages": compact_messages,
            "optical_state": "locked" if link_active else "blocked",
            **aircraft_details,
        },
        "aircraft_link": link_active,
        "aircraft_age": round(age, 1) if age is not None else None,
        "aircraft_packets": int(doc.get("packets_received", 0) or 0),
        "aircraft_msg": last_message,
        "aircraft_msg_time": last_message_time,
        "aircraft_mode": aircraft_mode,
        "aircraft_armed": aircraft_armed,
        **{
            f"aircraft_{key}": value
            for key, value in aircraft_details.items()
            if value is not None
        },
        "aircraft_optical_state": "locked" if link_active else "blocked",
    }


def read_optical_state(path: Path = AIRCRAFT_STATE_PATH) -> str:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "blocked"
    explicit = str(
        doc.get("optical_state", doc.get("link_state", ""))
    ).strip().lower()
    if explicit in ("locked", "blocked"):
        return explicit
    messages = doc.get("messages") if isinstance(doc.get("messages"), list) else []
    for item in reversed(messages[-6:]):
        if not isinstance(item, dict):
            continue
        text = (
            str(item.get("type", "")) + " " + str(item.get("text", ""))
        ).upper()
        if "LINK_BLOCKED" in text or "SIGNAL_INTERRUPTED" in text:
            return "blocked"
    updated_at = float(doc.get("updated_at", 0) or 0)
    return "locked" if updated_at and time.time() - updated_at < 3 else "blocked"


def aircraft_report_signature(summary: dict) -> tuple:
    return (
        bool(summary.get("aircraft_link")),
        str(summary.get("aircraft_mode", "UNKNOWN")),
        summary.get("aircraft_armed"),
    )


def start_aircraft_state_gateway(config: dict, transport_port: int = 14560):
    ports = legacy_gateway_ports(config, transport_port)
    print(f"starting aircraft UDP gateway ports={ports} state={AIRCRAFT_STATE_PATH}", flush=True)
    return start_aircraft_gateway(ports, AIRCRAFT_STATE_PATH)


def run_agent(config: dict) -> int:
    ensure_l610_usbnet(config["at_port"])
    device_id = config["device_id"]
    report_topic = make_topic(device_id, "thing/property/report")
    report_response_topic = make_topic(device_id, "thing/property/report_response")
    property_set_topic = make_topic(device_id, "thing/property/set")

    client = mqtt_client(config)
    rover = RoverMavlink(discover_mavlink_urls(config.get("mavlink_url")), int(config.get("mavlink_baud", 115200)))
    telemetry = RoverTelemetry()
    state_path = Path(config["state_path"])
    command_path = Path(config["command_path"])
    aircraft_local_port = int(config.get("aircraft_link_local_port", 14560))
    aircraft_gateway = start_aircraft_state_gateway(config, aircraft_local_port)
    connected = False
    pending: list[CloudCommand] = []
    manual_active_until = 0.0
    aircraft_link = AircraftLink(
        max_attempts=int(config.get("aircraft_max_attempts", 4)),
        retry_interval=float(config.get("aircraft_retry_interval_sec", 0.5)),
    )
    aircraft_transport = AircraftTransport(
        aircraft_link,
        local_host=str(config.get("aircraft_link_local_host", "0.0.0.0")),
        local_port=aircraft_local_port,
        peer=(
            str(config.get("aircraft_peer_host", "192.168.4.1")),
            int(config.get("aircraft_peer_port", 14555)),
        ),
        command_peer=(
            str(config.get("aircraft_peer_host", "192.168.4.1")),
            int(config.get("aircraft_command_peer_port", 14560)),
        ),
        psk=config.get("aircraft_link_psk") or os.environ.get(
            "AIRCRAFT_LINK_PSK"
        ),
        auth_store=AtomicJsonStore(config["aircraft_auth_state_path"]),
        packet_observer=lambda data, remote: aircraft_gateway.record_packet(
            aircraft_local_port,
            data,
            remote,
        ),
    )

    def aircraft_optical_state():
        if aircraft_transport.optical_state() == "locked":
            return "locked"
        try:
            if aircraft_gateway.snapshot().get("link_active"):
                return "locked"
        except Exception:
            pass
        return "blocked"

    class RoverExecutor:
        def execute(self, command):
            if command.action == "mission":
                items = command.payload.get("items")
                if not isinstance(items, list):
                    raise ValueError("rover mission requires items")
                status = rover.queue_mission(
                    str(command.command_id),
                    items,
                    telemetry,
                    source_timestamp=command.source_timestamp,
                )
                telemetry.mission_status = "mission queued"
                return {
                    "accepted": True,
                    "stage": status.stage,
                    "message": telemetry.mission_status,
                }
            rover_command = RoverCommand.from_dict(
                {"command": command.action, **command.payload},
                source="command_router",
            )
            ok, message = rover.apply(rover_command, telemetry)
            return {
                "accepted": ok,
                "stage": "executed" if ok else "failed",
                "message": message,
                "rover_command": rover_command,
            }

    router = CommandRouter(
        RoverExecutor(),
        aircraft_transport,
        optical_state=aircraft_optical_state,
        system_executor=NetworkModeExecutor(config["network_mode_request_path"]),
        max_age_seconds=float(config.get("command_max_age_sec", 10.0)),
    )

    def on_connect(client, userdata, flags, rc, properties=None):
        nonlocal connected
        connected = rc == 0
        print(f"MQTT connected rc={rc}")
        client.subscribe(property_set_topic, qos=1)
        client.subscribe(report_response_topic, qos=1)

    def on_disconnect(client, userdata, rc, properties=None):
        nonlocal connected
        connected = False
        print(f"MQTT disconnected rc={rc}")

    def on_message(client, userdata, msg):
        if msg.topic == report_response_topic:
            print(f"property report response {msg.payload.decode(errors='ignore')}", flush=True)
            return
        data = extract_property_set(msg.payload)
        command = CloudCommand.from_cloud(data)
        pending.append(command)
        print(f"cloud command {command}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect(config["host"], int(config["port"]), keepalive=60)
    client.loop_start()

    next_report = 0.0
    last_aircraft_report_signature = None
    last_aircraft_diagnostic = None
    report_interval = float(config["report_interval_sec"])
    next_lte = 0.0
    while True:
        now = time.time()
        try:
            for command in consume_local_commands(command_path):
                pending.append(command)
            while pending:
                command = pending.pop(0)
                try:
                    result = router.route(command)
                    ok = bool(result.get("accepted", True))
                    message = str(result.get("message", result.get("stage", "routed")))
                except (CommandRejected, MissionError, TypeError, ValueError) as exc:
                    ok, message = False, str(exc)
                    result = {"stage": "rejected"}
                telemetry.last_command = (
                    f"aircraft_{command.action}"
                    if command.target == "aircraft"
                    else command.action
                )
                record_command_receipt(
                    telemetry,
                    target=command.target,
                    command_id=str(command.command_id),
                    accepted=ok,
                    stage=str(result.get("stage", "unknown")),
                    message=message,
                )
                rover_command = result.get("rover_command")
                if ok and rover_command and rover_command.command in ("manual", "drive") and (rover_command.steering or rover_command.throttle):
                    manual_active_until = time.time() + 1.8
                if ok and rover_command and rover_command.command in ("stop", "hold", "brake", "arm"):
                    manual_active_until = 0.0
                print(
                    f"routed command id={command.command_id} target={command.target} "
                    f"action={command.action} ok={ok} stage={result.get('stage')} "
                    f"message={message}",
                    flush=True,
                )
            transaction = aircraft_transport.pump(time.monotonic())
            telemetry.apply_aircraft_transaction(transaction)
            diagnostic = aircraft_transport.status()
            diagnostic_key = (
                diagnostic.get("optical_state"),
                diagnostic.get("rx_packets"),
                diagnostic.get("rx_auth_errors"),
                diagnostic.get("rx_frame_errors"),
                diagnostic.get("last_remote"),
            )
            if diagnostic_key != last_aircraft_diagnostic:
                print(f"aircraft transport {diagnostic}", flush=True)
                last_aircraft_diagnostic = diagnostic_key
            for mission_status in rover.drain_mission_results():
                receipt = apply_rover_mission_status(telemetry, mission_status)
                print(
                    "rover mission result "
                    f"id={receipt['command_id']} stage={receipt['stage']} "
                    f"error={receipt['error_type']} message={receipt['message']}",
                    flush=True,
                )
            if telemetry.last_command in ("stop", "arm") and rover.connect(timeout=0.05):
                rover.try_neutral()
            if telemetry.last_command in ("manual", "drive") and (telemetry.steering or telemetry.throttle) and time.time() > manual_active_until:
                if rover.connect(timeout=0.05):
                    rover.try_stop()
                telemetry.steering = 0
                telemetry.throttle = 0
                telemetry.control_mode = "standby"
                telemetry.last_command = "stop"
                telemetry.mission_status = "manual timeout stopped"
                telemetry.fault_text = ""
            telemetry = rover.update_telemetry(telemetry)
            manual_is_active = telemetry.last_command in ("manual", "drive") and time.time() <= manual_active_until
            if now >= next_lte and not manual_is_active:
                telemetry.lte_rssi = read_lte_rssi(config["at_port"])
                next_lte = now + 10
            write_state(state_path, telemetry, connected)
            aircraft_summary = read_aircraft_summary(
                transport_status=aircraft_transport.status()
            )
            aircraft_signature = aircraft_report_signature(aircraft_summary)
            aircraft_changed = (
                last_aircraft_report_signature is not None
                and aircraft_signature != last_aircraft_report_signature
            )
            if now >= next_report or aircraft_changed:
                payload = telemetry.tuya_compact_payload(
                    aircraft_summary
                )
                info = client.publish(report_topic, json.dumps(payload, separators=(",", ":")), qos=1)
                next_report = now + report_interval
                last_aircraft_report_signature = aircraft_signature
                print(f"published {report_topic} rc={info.rc} msgId={payload['msgId']}", flush=True)
        except Exception as exc:
            telemetry.fault_text = f"agent loop error: {exc}"
            write_state(state_path, telemetry, connected)
            print(f"agent loop error: {exc}", flush=True)
        time.sleep(0.1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RDK X5 + L610 + TuyaLink Rover Agent")
    parser.add_argument("command", choices=["run", "check-l610", "dry-auth"])
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--at-port", default="/dev/ttyUSB0")
    args = parser.parse_args(argv)
    if args.command == "check-l610":
        return check_l610(args.at_port)
    config = load_config(Path(args.config))
    if args.command == "dry-auth":
        creds = build_tuya_credentials(config["device_id"], config["device_secret"], 1700000000)
        print(json.dumps({"client_id": creds["client_id"], "username": creds["username"], "password_len": len(creds["password"])}, indent=2))
        return 0
    return run_agent(config)


if __name__ == "__main__":
    raise SystemExit(main())
