#!/usr/bin/env bash
set -Eeuo pipefail

REQUEST_PATH="${SLAVE_NETWORK_REQUEST_PATH:-/run/low-altitude-slave/network-mode}"
PHONE_WIFI_PROFILE="${PHONE_WIFI_PROFILE:-Mr.think的Mate 70 Pro+}"

[[ -f "$REQUEST_PATH" ]] || exit 0
mode="$(python3 - "$REQUEST_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    request = json.load(stream)
if set(request) != {"mode", "requested_at", "source"}:
    raise SystemExit("invalid network_phone request fields")
if request["mode"] != "phone" or request["source"] != "aircraft_2":
    raise SystemExit("invalid network_phone request")
print(request["mode"])
PY
)"
[[ "$mode" == phone ]] || { echo "unsupported network mode" >&2; exit 1; }

rm -f "$REQUEST_PATH"
nmcli connection up "$PHONE_WIFI_PROFILE" ifname wlan0
