#!/usr/bin/env python3
"""Snapshot, configure, verify, and restore ArduCopter Follow parameters."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import time


FOLLOW_VALUES = {
    "SYSID_THISMAV": 2,
    "FOLL_ENABLE": 1,
    "FOLL_SYSID": 1,
    "FOLL_DIST_MAX": 30,
    "FOLL_OFS_TYPE": 1,
    "FOLL_OFS_X": 0,
    "FOLL_OFS_Y": -5,
    "FOLL_OFS_Z": 0,
    "FOLL_ALT_TYPE": 1,
}

COPTER_MAV_TYPES = frozenset((2, 3, 4, 13, 14, 15, 29, 35))


class FollowConfigurationError(RuntimeError):
    pass


def _field(message, name, default=None):
    return getattr(message, name, default)


def _parameter_name(message):
    raw = _field(message, "param_id", "")
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", "ignore")
    return str(raw).rstrip("\x00").strip().upper()


def _read_parameter(connection, name, timeout, *, expected=None):
    target_system = int(getattr(connection, "target_system", 1) or 1)
    target_component = int(getattr(connection, "target_component", 1) or 1)
    encoded = name.encode("ascii")
    last_observed = None
    for _attempt in range(3):
        connection.mav.param_request_read_send(
            target_system,
            target_component,
            encoded,
            -1,
        )
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            message = connection.recv_match(
                type=["PARAM_VALUE"],
                blocking=True,
                timeout=max(0.01, deadline - time.monotonic()),
            )
            if message is None:
                break
            if _parameter_name(message) == name:
                observed = {
                    "value": float(_field(message, "param_value", 0.0)),
                    "type": int(_field(message, "param_type", 9) or 9),
                }
                last_observed = observed
                if expected is None or abs(observed["value"] - float(expected)) <= 1e-4:
                    return observed
    if last_observed is not None:
        return last_observed
    raise FollowConfigurationError(f"missing parameter {name}")


def _validate_vehicle(connection, device, timeout):
    if not str(device).startswith("/dev/serial/by-id/"):
        raise FollowConfigurationError("fixed /dev/serial/by-id device is required")
    heartbeat = connection.wait_heartbeat(timeout=float(timeout))
    if heartbeat is None:
        raise FollowConfigurationError("flight controller heartbeat missing")
    if int(_field(heartbeat, "type", -1)) not in COPTER_MAV_TYPES:
        raise FollowConfigurationError("flight controller is not ArduCopter/Copter")
    if int(_field(heartbeat, "base_mode", 0) or 0) & 128:
        raise FollowConfigurationError("flight controller must be disarmed")
    return heartbeat


def collect_follow_snapshot(connection, *, device, timeout=1.0):
    heartbeat = _validate_vehicle(connection, device, timeout)
    parameters = {
        name: _read_parameter(connection, name, timeout)
        for name in FOLLOW_VALUES
    }
    return {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "device": str(device),
        "vehicle": "Copter",
        "vehicle_type": int(_field(heartbeat, "type", -1)),
        "target_system": int(getattr(connection, "target_system", 1) or 1),
        "target_component": int(getattr(connection, "target_component", 1) or 1),
        "parameters": parameters,
        "desired": dict(FOLLOW_VALUES),
        "write_performed": False,
    }


def _application_order(values):
    names = [name for name in values if name != "SYSID_THISMAV"]
    if "SYSID_THISMAV" in values:
        names.append("SYSID_THISMAV")
    return names


def _write_and_verify(connection, name, value, param_type, timeout):
    target_system = int(getattr(connection, "target_system", 1) or 1)
    target_component = int(getattr(connection, "target_component", 1) or 1)
    connection.mav.param_set_send(
        target_system,
        target_component,
        name.encode("ascii"),
        float(value),
        int(param_type),
    )
    if name == "SYSID_THISMAV":
        connection.target_system = int(value)
    observed = _read_parameter(
        connection,
        name,
        timeout,
        expected=value,
    )
    if abs(observed["value"] - float(value)) > 1e-4:
        raise FollowConfigurationError(
            f"readback failed for {name}: expected {value}, got {observed['value']}"
        )
    return observed


def apply_parameter_values(connection, snapshot, values, *, timeout=1.0):
    parameters = snapshot.get("parameters", {})
    missing = set(values) - set(parameters)
    if missing:
        raise FollowConfigurationError(
            "snapshot missing parameters: " + ",".join(sorted(missing))
        )
    written = []
    try:
        for name in _application_order(values):
            # Include the in-flight parameter in rollback: a controller may
            # apply PARAM_SET before its confirming PARAM_VALUE reaches us.
            written.append(name)
            _write_and_verify(
                connection,
                name,
                values[name],
                parameters[name]["type"],
                timeout,
            )
    except Exception:
        for name in reversed(written):
            try:
                _write_and_verify(
                    connection,
                    name,
                    parameters[name]["value"],
                    parameters[name]["type"],
                    timeout,
                )
            except Exception:
                pass
        raise
    return {"verified": True, "written": written, "values": dict(values)}


def apply_follow_values(connection, snapshot, *, timeout=1.0):
    return apply_parameter_values(
        connection,
        snapshot,
        FOLLOW_VALUES,
        timeout=timeout,
    )


def _restore_script(snapshot_path):
    return f'''#!/usr/bin/env python3
import argparse, json, time
from pymavlink import mavutil

p = argparse.ArgumentParser()
p.add_argument("--connection", required=True)
p.add_argument("--baud", type=int, default=115200)
args = p.parse_args()
snapshot = json.load(open({str(snapshot_path)!r}, encoding="utf-8"))
connection = mavutil.mavlink_connection(args.connection, baud=args.baud, autoreconnect=True, source_system=255)
heartbeat = connection.wait_heartbeat(timeout=5)
if heartbeat is None or int(getattr(heartbeat, "base_mode", 0)) & 128:
    raise SystemExit("restore requires a connected, disarmed flight controller")
for name in [n for n in snapshot["parameters"] if n != "SYSID_THISMAV"] + ["SYSID_THISMAV"]:
    item = snapshot["parameters"][name]
    connection.mav.param_set_send(connection.target_system or 1, connection.target_component or 1, name.encode("ascii"), float(item["value"]), int(item["type"]))
    if name == "SYSID_THISMAV":
        connection.target_system = int(item["value"])
    time.sleep(0.15)
print("Follow parameters restored; verify readback before flight")
'''


def _atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def persist_recovery_bundle(snapshot, backup_dir):
    backup_dir = Path(backup_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    snapshot_path = backup_dir / f"follow-before-{stamp}.json"
    restore_path = backup_dir / f"restore-follow-{stamp}.py"
    _atomic_write(
        snapshot_path,
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        stat.S_IRUSR | stat.S_IWUSR,
    )
    _atomic_write(
        restore_path,
        _restore_script(snapshot_path),
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR,
    )
    return snapshot_path, restore_path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--backup-dir",
        default="/var/backups/low-altitude-iot/follow",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore")
    args = parser.parse_args(argv)
    if not args.connection.startswith("/dev/serial/by-id/"):
        parser.error("--connection must use /dev/serial/by-id/")

    from pymavlink import mavutil

    connection = mavutil.mavlink_connection(
        args.connection,
        baud=args.baud,
        autoreconnect=True,
        source_system=255,
    )
    if args.restore:
        snapshot = json.loads(Path(args.restore).read_text())
        _validate_vehicle(connection, args.connection, 5.0)
        original = {
            name: item["value"]
            for name, item in snapshot["parameters"].items()
        }
        result = apply_parameter_values(connection, snapshot, original, timeout=1.0)
        print(json.dumps({"mode": "restore", **result}, sort_keys=True))
        return 0

    snapshot = collect_follow_snapshot(
        connection,
        device=args.connection,
        timeout=1.0,
    )
    snapshot_path, restore_path = persist_recovery_bundle(
        snapshot,
        args.backup_dir,
    )
    result = {"mode": "verify-only", "verified": True, "written": []}
    if args.apply:
        result = {"mode": "apply", **apply_follow_values(connection, snapshot)}
    print(json.dumps({
        **result,
        "snapshot": str(snapshot_path),
        "restore": str(restore_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
