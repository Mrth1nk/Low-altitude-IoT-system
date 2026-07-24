#!/usr/bin/env python3
import argparse
import json
import os
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
from rover_state import (
    RoverCommand,
    RoverTelemetry,
    apply_rover_mission_status,
    record_command_receipt,
)
from rover_mission import MissionError
from tuya_auth import build_tuya_credentials, make_topic

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


def read_aircraft_summary(path: Path = AIRCRAFT_STATE_PATH) -> dict:
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
        }
        for item in messages[-6:]
        if isinstance(item, dict)
    ]
    updated_at = float(doc.get("updated_at", 0) or 0)
    age = time.time() - updated_at if updated_at else None
    link_active = bool(age is not None and age < 3)
    last_message = ""
    last_message_time = None
    if compact_messages:
        item = compact_messages[-1]
        last_message = f"{item.get('type', 'MSG')} {item.get('text', '')}"[:160]
        last_message_time = item.get("time")
    return {
        "aircraft": {
            "link_active": link_active,
            "last_seen_age_sec": round(age, 2) if age is not None else None,
            "last_remote": str(doc.get("last_remote", ""))[:64],
            "packets_received": int(doc.get("packets_received", 0) or 0),
            "bytes_received": int(doc.get("bytes_received", 0) or 0),
            "messages": compact_messages,
        },
        "aircraft_link": link_active,
        "aircraft_age": round(age, 1) if age is not None else None,
        "aircraft_packets": int(doc.get("packets_received", 0) or 0),
        "aircraft_msg": last_message,
        "aircraft_msg_time": last_message_time,
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
        psk=config.get("aircraft_link_psk") or os.environ.get(
            "AIRCRAFT_LINK_PSK"
        ),
    )

    class RoverExecutor:
        def execute(self, command):
            if command.action == "mission":
                items = command.payload.get("items")
                if not isinstance(items, list):
                    raise ValueError("rover mission requires items")
                status = rover.queue_mission(
                    str(command.command_id), items, telemetry
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
        aircraft_link,
        optical_state=aircraft_transport.optical_state,
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
            if now >= next_report:
                payload = telemetry.tuya_compact_payload(read_aircraft_summary())
                info = client.publish(report_topic, json.dumps(payload, separators=(",", ":")), qos=1)
                next_report = now + report_interval
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
