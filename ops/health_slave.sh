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

FC_DEVICE="${SLAVE_FC_DEVICE:-}"
PEER_HOST="${SLAVE_ROVER_IP:-192.168.4.2}"
LOCAL_PORT="${SLAVE_LOCAL_PORT:-14620}"
HEARTBEAT_TIMEOUT="${SLAVE_HEARTBEAT_TIMEOUT:-5}"

if ((DRY_RUN)); then
  cat <<EOF
CHECK fixed /dev/serial/by-id flight-controller identity
CHECK no competing serial owner
CHECK Copter heartbeat within ${HEARTBEAT_TIMEOUT}s
CHECK UDP peer ${PEER_HOST}, local port ${LOCAL_PORT}
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
from pymavlink import mavutil

device, timeout = sys.argv[1], float(sys.argv[2])
link = mavutil.mavlink_connection(device, autoreconnect=False)
try:
    heartbeat = link.wait_heartbeat(timeout=timeout)
    vehicle_type = int(getattr(heartbeat, "type", -1))
    copter_types = {
        mavutil.mavlink.MAV_TYPE_QUADROTOR,
        mavutil.mavlink.MAV_TYPE_HEXAROTOR,
        mavutil.mavlink.MAV_TYPE_OCTOROTOR,
        mavutil.mavlink.MAV_TYPE_TRICOPTER,
        mavutil.mavlink.MAV_TYPE_HELICOPTER,
    }
    if vehicle_type not in copter_types:
        raise SystemExit(f"FAIL: heartbeat is not Copter type ({vehicle_type})")
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
echo "OK: slave service active; FC=$FC_DEVICE peer=$PEER_HOST port=$LOCAL_PORT"
