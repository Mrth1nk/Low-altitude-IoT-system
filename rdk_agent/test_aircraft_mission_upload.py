#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from aircraft_commands import TEST_HOME_LAT, TEST_HOME_LNG, send_aircraft_command


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a test aircraft mission through the current RDK mengchuang link.")
    parser.add_argument("--state", default=str(Path.home() / "uav_tuya_agent" / "aircraft_state.json"))
    parser.add_argument("--lat", type=float, default=32.11985)
    parser.add_argument("--lng", type=float, default=118.95875)
    parser.add_argument("--alt", type=float, default=20.0)
    parser.add_argument("--real-home", action="store_true", help="Do not set the temporary NJU test Home/EKF origin.")
    args = parser.parse_args()

    command = "aircraft_goto" if args.real_home else "aircraft_goto_test_home"
    print(f"[mission-test] command={command} target={args.lat:.7f},{args.lng:.7f},{args.alt:.1f}m")
    if not args.real_home:
        print(f"[mission-test] temporary home/origin={TEST_HOME_LAT:.7f},{TEST_HOME_LNG:.7f}")
    message = send_aircraft_command(
        command,
        Path(args.state),
        target_lat=args.lat,
        target_lng=args.lng,
        target_alt=args.alt,
    )
    print(f"[mission-test] {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
