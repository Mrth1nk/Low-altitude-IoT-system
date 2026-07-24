#!/usr/bin/env python3
from l610 import at_command, check_l610, ensure_l610_usbnet as ensure_l610_rndis, read_lte_rssi
from rover_state import RoverTelemetry as Telemetry
from rover_state import RoverTelemetry
from tuya_auth import build_tuya_credentials, make_topic
from tuya_rover_agent import load_config, main, mqtt_client, run_agent


def make_report_payload(t: RoverTelemetry) -> dict:
    return t.tuya_report_payload()


if __name__ == "__main__":
    raise SystemExit(main())
