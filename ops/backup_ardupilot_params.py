#!/usr/bin/env python3
"""Read and persist a complete ArduPilot parameter snapshot.

This tool is read-only. It never calls PARAM_SET or any parameter mutation API.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time


class SnapshotError(ValueError):
    pass


def _kind(message):
    getter = getattr(message, "get_type", None)
    return getter() if getter else str(getattr(message, "type", ""))


def _field(message, name, default=None):
    return getattr(message, name, default)


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise SnapshotError("snapshot must be an object")
    for key in ("vehicle", "firmware", "parameters"):
        if key not in snapshot:
            raise SnapshotError(f"snapshot missing {key}")
    if not isinstance(snapshot["vehicle"], str) or not snapshot["vehicle"]:
        raise SnapshotError("vehicle must be non-empty")
    if not isinstance(snapshot["firmware"], str) or not snapshot["firmware"]:
        raise SnapshotError("firmware must be non-empty")
    parameters = snapshot["parameters"]
    if not isinstance(parameters, dict) or not parameters:
        raise SnapshotError("parameters must be a non-empty object")
    normalized = [str(name).strip().upper() for name in parameters]
    if any(not name for name in normalized):
        raise SnapshotError("parameter identity must be non-empty")
    if len(set(normalized)) != len(normalized):
        raise SnapshotError("duplicate parameter identity")
    for name, item in parameters.items():
        if not isinstance(item, dict) or "value" not in item or "type" not in item:
            raise SnapshotError(f"parameter {name} is incomplete")
    return snapshot


def collect_snapshot(connection, *, vehicle, firmware, timeout=2.0):
    heartbeat = connection.wait_heartbeat(timeout=float(timeout))
    if _kind(heartbeat) != "HEARTBEAT":
        raise SnapshotError("connection did not return a heartbeat")

    mav = getattr(connection, "mav", None)
    request_list = getattr(mav, "param_request_list_send", None)
    if callable(request_list):
        request_list(
            int(getattr(connection, "target_system", 1) or 1),
            int(getattr(connection, "target_component", 1) or 1),
        )

    parameters = {}
    expected_count = None
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        message = connection.recv_match(
            type=["PARAM_VALUE"], blocking=True, timeout=remaining
        )
        if message is None:
            break
        name = str(_field(message, "param_id", "")).strip().rstrip("\x00").upper()
        if not name:
            raise SnapshotError("PARAM_VALUE has empty param_id")
        if name in parameters:
            raise SnapshotError(f"duplicate PARAM_VALUE: {name}")
        parameters[name] = {
            "value": float(_field(message, "param_value")),
            "type": int(_field(message, "param_type")),
            "index": int(_field(message, "param_index", len(parameters))),
        }
        count = _field(message, "param_count", None)
        if count is not None:
            expected_count = int(count)
        if expected_count is not None and len(parameters) >= expected_count:
            break

    if expected_count is not None and len(parameters) != expected_count:
        raise SnapshotError(
            f"incomplete parameter snapshot expected={expected_count} actual={len(parameters)}"
        )
    snapshot = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "vehicle": str(vehicle),
        "firmware": str(firmware),
        "parameter_count": len(parameters),
        "parameters": parameters,
        "write_performed": False,
    }
    return validate_snapshot(snapshot)


def _write_snapshot(path, snapshot):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection")
    parser.add_argument("--output", required=True)
    parser.add_argument("--vehicle", required=True)
    parser.add_argument("--firmware", required=True)
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="connect and read parameters; still never writes",
    )
    args = parser.parse_args(argv)
    if not args.read_only:
        print(
            "DRY-RUN: would read the complete parameter set; no connection opened"
        )
        return 0
    if not args.connection:
        parser.error("--connection is required with --read-only")
    from pymavlink import mavutil

    connection = mavutil.mavlink_connection(args.connection)
    snapshot = collect_snapshot(
        connection,
        vehicle=args.vehicle,
        firmware=args.firmware,
    )
    _write_snapshot(Path(args.output), snapshot)
    print(f"READ-ONLY BACKUP: {snapshot['parameter_count']} parameters -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
