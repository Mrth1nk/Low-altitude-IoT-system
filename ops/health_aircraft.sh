#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
if ((!DRY_RUN)) && [[ -f /etc/low-altitude-iot/aircraft.env ]]; then
  set -a
  # Root-owned configuration supplies the actual by-id camera path.
  source /etc/low-altitude-iot/aircraft.env
  set +a
fi
RUNTIME_DIR="${AIRCRAFT_RUNTIME_DIR:-/run/low-altitude-iot}"
HEALTH="$RUNTIME_DIR/aircraft-health.json"
OPTICAL="$RUNTIME_DIR/optical-state.json"

if ((DRY_RUN)); then
  cat <<EOF
CHECK low-altitude-aircraft.service active and low-altitude-vision.service active
CHECK atomic snapshot $HEALTH exists without temporary sibling
CHECK fields command_id queue_depth fc_heartbeat_at optical last_error follow_target and fresh timestamp
CHECK optical atomic state $OPTICAL is fresh and structurally valid
CHECK /proc MainPID fd roles: aircraft owns ttyACM0/ttyUSB0 only
CHECK /proc MainPID fd roles: vision owns ttyS9/configured camera only
EOF
  exit 0
fi

for service in low-altitude-aircraft.service low-altitude-vision.service; do
  systemctl is-active --quiet "$service" || {
    echo "FAIL: $service is not active" >&2
    exit 1
  }
done
[[ -f "$HEALTH" && ! -e "$HEALTH.tmp" ]] || {
  echo "FAIL: aircraft atomic health snapshot missing/incomplete" >&2
  exit 1
}
[[ -f "$OPTICAL" && ! -e "$OPTICAL.tmp" ]] || {
  echo "FAIL: optical atomic state missing/incomplete" >&2
  exit 1
}

python3 - "$HEALTH" "$OPTICAL" <<'PY'
import json
from pathlib import Path
import sys
import time

health = json.loads(Path(sys.argv[1]).read_text())
optical = json.loads(Path(sys.argv[2]).read_text())
required = {
    "timestamp", "command_id", "queue_depth", "fc_heartbeat_at",
    "optical", "last_error",
}
if not required <= set(health):
    raise SystemExit("FAIL: health snapshot fields invalid")
follow = health.get("follow_target")
if not isinstance(follow, dict):
    raise SystemExit("FAIL: follow_target health missing")
for field in ("produced", "stale", "dropped"):
    if not isinstance(follow.get(field), int) or follow[field] < 0:
        raise SystemExit(f"FAIL: follow_target {field} invalid")
now = time.time()
if now - float(health["timestamp"]) > 2.0:
    raise SystemExit("FAIL: health snapshot stale")
if now - float(health["fc_heartbeat_at"]) > 5.0:
    raise SystemExit("FAIL: flight-controller heartbeat stale")
if now - float(optical.get("timestamp", 0.0)) > 2.0:
    raise SystemExit("FAIL: optical state stale")
if not isinstance(health["queue_depth"], int) or health["queue_depth"] < 0:
    raise SystemExit("FAIL: queue depth invalid")
PY

check_fd_roles() {
  local service="$1"; shift
  local pid fd resolved expected
  pid="$(systemctl show -p MainPID --value "$service")"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  for expected in "$@"; do
    found=0
    for fd in "/proc/$pid/fd/"*; do
      resolved="$(readlink -f "$fd" 2>/dev/null || true)"
      [[ "$resolved" == "$expected" ]] && found=1
    done
    ((found)) || {
      echo "FAIL: $service does not own $expected" >&2
      return 1
    }
  done
}

camera_device="$(readlink -f "${VISION_CAMERA_DEVICE:-/dev/video0}")"
check_fd_roles low-altitude-aircraft.service /dev/ttyACM0 /dev/ttyUSB0
check_fd_roles low-altitude-vision.service /dev/ttyS9 "$camera_device"
echo "OK: aircraft services, atomic snapshots and device roles are healthy"
