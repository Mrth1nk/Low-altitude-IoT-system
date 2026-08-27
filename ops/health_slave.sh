#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
PREFLIGHT=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --preflight) PREFLIGHT=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "${SLAVE_FC_DEVICE:-}" && -r /etc/low-altitude-iot/slave.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /etc/low-altitude-iot/slave.env
  set +a
fi

FC_DEVICE="${SLAVE_FC_DEVICE:-}"
PEER_HOST="${SLAVE_ROVER_IP:-192.168.4.2}"
LOCAL_PORT="${SLAVE_LOCAL_PORT:-14620}"
FOLLOW_PORT="${SLAVE_FOLLOW_PORT:-14630}"
HEARTBEAT_TIMEOUT="${SLAVE_HEARTBEAT_TIMEOUT:-5}"
HEALTH="${SLAVE_RUNTIME_DIR:-/run/low-altitude-slave}/slave-health.json"

if ((DRY_RUN)); then
  cat <<EOF
CHECK fixed /dev/serial/by-id flight-controller identity
CHECK no competing serial owner
CHECK Copter heartbeat within ${HEARTBEAT_TIMEOUT}s
CHECK UDP peer ${PEER_HOST}, local port ${LOCAL_PORT}
CHECK FOLLOW_TARGET UDP local port ${FOLLOW_PORT} and health counters
CHECK low-altitude-slave.service when not in preflight mode
EOF
  exit 0
fi

[[ "$FC_DEVICE" == /dev/serial/by-id/* ]] || {
  echo "FAIL: SLAVE_FC_DEVICE must use /dev/serial/by-id/" >&2; exit 1;
}
[[ -L "$FC_DEVICE" || -e "$FC_DEVICE" ]] || {
  echo "FAIL: flight-controller by-id device is missing" >&2; exit 1;
}
resolved="$(readlink -f "$FC_DEVICE")"

owners="$(lsof -t "$resolved" 2>/dev/null || true)"
if [[ -n "$owners" ]]; then
  allowed=1
  for pid in $owners; do
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    [[ "$cmd" == *"slave_agent.main"* ]] || allowed=0
  done
  ((allowed)) || { echo "FAIL: competing serial owner: $owners" >&2; exit 1; }
fi

if ((PREFLIGHT)); then
  /usr/bin/python3 - "$FC_DEVICE" "$HEARTBEAT_TIMEOUT" <<'PY'
import sys
import time
from pymavlink import mavutil

device, timeout = sys.argv[1], float(sys.argv[2])
link = mavutil.mavlink_connection(device, autoreconnect=False)
try:
    copter_types = {
        mavutil.mavlink.MAV_TYPE_QUADROTOR,
        mavutil.mavlink.MAV_TYPE_HEXAROTOR,
        mavutil.mavlink.MAV_TYPE_OCTOROTOR,
        mavutil.mavlink.MAV_TYPE_TRICOPTER,
        mavutil.mavlink.MAV_TYPE_HELICOPTER,
    }
    deadline = time.monotonic() + timeout
    heartbeat = None
    while time.monotonic() < deadline:
        candidate = link.recv_match(type="HEARTBEAT", blocking=True, timeout=max(0.01, deadline - time.monotonic()))
        if candidate is None:
            break
        if int(getattr(candidate, "type", -1)) in copter_types:
            heartbeat = candidate
            break
    if heartbeat is None:
        raise SystemExit("FAIL: Copter heartbeat missing")
finally:
    link.close()
PY
  exit 0
fi

systemctl is-active --quiet low-altitude-slave.service || {
  echo "FAIL: low-altitude-slave.service inactive" >&2; exit 1;
}
ss -lun | grep -Eq ":[[:space:]]*${LOCAL_PORT}[[:space:]]|:${LOCAL_PORT}[[:space:]]" || {
  echo "FAIL: slave UDP port ${LOCAL_PORT} is not listening" >&2; exit 1;
}
ss -lun | grep -Eq ":[[:space:]]*${FOLLOW_PORT}[[:space:]]|:${FOLLOW_PORT}[[:space:]]" || {
  echo "FAIL: FOLLOW_TARGET UDP port ${FOLLOW_PORT} is not listening" >&2; exit 1;
}
python3 - "$HEALTH" <<'PY'
import json
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit("FAIL: slave health snapshot missing")
health = json.loads(path.read_text())
if time.time() - float(health.get("timestamp", 0.0)) > 3.0:
    raise SystemExit("FAIL: slave health snapshot stale")
follow = health.get("follow_target")
if not isinstance(follow, dict):
    raise SystemExit("FAIL: follow_target health missing")
for field in ("received", "rejected", "write_errors"):
    if not isinstance(follow.get(field), int) or follow[field] < 0:
        raise SystemExit(f"FAIL: follow_target {field} invalid")
PY
echo "OK: slave service active; FC=$FC_DEVICE peer=$PEER_HOST ports=$LOCAL_PORT/$FOLLOW_PORT"
