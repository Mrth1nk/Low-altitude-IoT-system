#!/usr/bin/env python3
"""Serial device discovery for flight controller and Wi-Fi telemetry adapters."""

from __future__ import annotations

import argparse
import glob
import os
import time
from pathlib import Path

from mavlink_monitor import MAVLinkMonitor


COMMON_BAUDS = [57600, 115200, 921600]


def list_serial_devices() -> list[str]:
    paths = set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    for link in glob.glob("/dev/serial/by-id/*"):
        try:
            paths.add(str(Path(link).resolve()))
        except OSError:
            pass
    return sorted(paths)


def classify_by_name(path: str) -> str:
    lower = path.lower()
    if any(token in lower for token in ["ardupilot", "px4", "fmuv", "cube", "pixhawk", "px4_fmu"]):
        return "possible flight controller"
    if any(token in lower for token in ["ch340", "cp210", "ftdi", "silicon", "wifi", "telemetry"]):
        return "possible WiFi telemetry module"
    return "unknown serial device"


def has_mavlink_heartbeat(path: str, baud: int, timeout: float = 2.0) -> bool:
    try:
        import serial  # type: ignore

        port = serial.Serial(path, baudrate=baud, timeout=0.05)
    except Exception:
        return False
    monitor = MAVLinkMonitor()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            data = port.read(1024)
            if data and any(event["type"] == "HEARTBEAT" for event in monitor.feed(data)):
                return True
    finally:
        port.close()
    return False


def detect_devices() -> list[dict]:
    rows = []
    for path in list_serial_devices():
        role = classify_by_name(path)
        heartbeat_baud = None
        for baud in COMMON_BAUDS:
            if has_mavlink_heartbeat(path, baud, timeout=0.5):
                role = "flight controller heartbeat detected"
                heartbeat_baud = baud
                break
        rows.append({"path": path, "role": role, "baud": heartbeat_baud})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="List possible OnboardBridge serial devices")
    parser.add_argument("--probe", action="store_true", help="try to detect MAVLink heartbeat")
    args = parser.parse_args()
    rows = detect_devices() if args.probe else [{"path": p, "role": classify_by_name(p), "baud": None} for p in list_serial_devices()]
    print("Detected serial devices:")
    for index, row in enumerate(rows):
        suffix = f" baud={row['baud']}" if row.get("baud") else ""
        print(f"[{index}] {row['path']}  {row['role']}{suffix}")
    if not rows:
        print("No /dev/ttyACM*, /dev/ttyUSB*, or /dev/serial/by-id devices found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
