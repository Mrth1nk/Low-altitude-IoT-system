#!/usr/bin/env python3
"""Onboard MAVLink bridge entrypoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from device_finder import detect_devices, list_serial_devices
from logger import setup_logger
from optical_link_state import DEFAULT_STATUS_FILE
from serial_bridge import SerialBridge


ROOT = Path(__file__).resolve().parent


def load_config(path: str | None = None) -> dict:
    config_path = Path(path) if path else ROOT / "config.json"
    return json.loads(config_path.read_text(encoding="utf-8"))


def choose_auto_devices(config: dict) -> tuple[str, str]:
    rows = detect_devices()
    fc = next((row["path"] for row in rows if "flight controller" in row["role"]), None)
    devices = [row["path"] for row in rows]
    wifi = next((path for path in devices if path != fc), None)
    return fc or config["flight_controller"]["port"], wifi or config["wifi_telemetry"]["port"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UAV WiFi telemetry MAVLink bridge")
    parser.add_argument("--config")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--fc")
    parser.add_argument("--fc-baud", type=int)
    parser.add_argument("--wifi")
    parser.add_argument("--wifi-baud", type=int)
    parser.add_argument("--wifi-mode", choices=["udp", "serial"], help="default follows Station Ctrl+Shift+W: udp")
    parser.add_argument("--udp-target-host")
    parser.add_argument("--udp-target-port", type=int)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--optical-status-file", default=DEFAULT_STATUS_FILE)
    parser.add_argument("--optical-status-stale-sec", type=float, default=2.0)
    parser.add_argument("--log")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)

    if args.list_devices:
        print("Detected serial devices:")
        for index, path in enumerate(list_serial_devices()):
            print(f"[{index}] {path}")
        return 0

    fc_port = args.fc or config["flight_controller"]["port"]
    wifi_port = args.wifi or config["wifi_telemetry"]["port"]
    if args.auto:
        fc_port, wifi_port = choose_auto_devices(config)

    fc_baud = args.fc_baud or int(config["flight_controller"]["baud"])
    wifi_baud = args.wifi_baud or int(config["wifi_telemetry"]["baud"])
    wifi_mode = args.wifi_mode or config["wifi_telemetry"]["mode"]
    log = setup_logger(log_path=args.log)

    bridge = SerialBridge(
        fc_port=fc_port,
        fc_baud=fc_baud,
        wifi_port=wifi_port,
        wifi_baud=wifi_baud,
        wifi_mode=wifi_mode,
        udp_host=config["wifi_telemetry"]["udp_host"],
        udp_port=int(config["wifi_telemetry"]["udp_port"]),
        udp_target_host=args.udp_target_host or config["wifi_telemetry"]["udp_target_host"],
        udp_target_port=args.udp_target_port or int(config["wifi_telemetry"]["udp_target_port"]),
        read_chunk_size=int(config["bridge"]["read_chunk_size"]),
        serial_timeout=float(config["bridge"]["serial_timeout"]),
        reconnect_interval=float(config["bridge"]["reconnect_interval"]),
        stats_interval=float(config["bridge"]["stats_interval"]),
        optical_status_file=args.optical_status_file,
        optical_status_stale_sec=args.optical_status_stale_sec,
        simulate=args.simulate,
        log=log,
    )
    if args.simulate:
        bridge.start()
        print("[OnboardBridge] simulate start/stop OK")
        bridge.stop()
        return 0
    bridge.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
