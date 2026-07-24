#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
ROLE=all
while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --role)
      ROLE="${2:?missing role}"
      shift
      ;;
    *)
      echo "usage: demo_preflight.sh [--dry-run] [--role rdk|elf|all]" >&2
      exit 2
      ;;
  esac
  shift
done
if [[ "$ROLE" != rdk && "$ROLE" != elf && "$ROLE" != all ]]; then
  echo "role must be rdk, elf, or all" >&2
  exit 2
fi

RDK_SERVICE="${RDK_SERVICE:-low-altitude-rdk.service}"
AIRCRAFT_SERVICE="${AIRCRAFT_SERVICE:-low-altitude-aircraft.service}"
VISION_SERVICE="${VISION_SERVICE:-low-altitude-vision.service}"
L610_PREFERRED="${L610_INTERFACE:-enxf04bb3b9ebe5}"
TUYA_PROBE="${TUYA_ROUTE_PROBE:-139.196.6.123}"
AIRCRAFT_HOST="${AIRCRAFT_HOST:-192.168.4.1}"
RDK_STATE="${RDK_STATE_FILE:-/home/sunrise/uav_tuya_agent/runtime_state.json}"
ELF_HEALTH="${ELF_HEALTH_FILE:-/run/low-altitude-iot/aircraft-health.json}"
ELF_INBOX="${ELF_INBOX_FILE:-/var/lib/low-altitude-iot/inbox.json}"
OPTICAL_STATE="${OPTICAL_STATE_FILE:-/run/low-altitude-iot/optical-state.json}"

pass=0
warn=0
fail=0

ok() {
  pass=$((pass + 1))
  printf 'PASS %-18s %s\n' "$1" "$2"
}

warning() {
  warn=$((warn + 1))
  printf 'WARN %-18s %s\n' "$1" "$2"
}

failure() {
  fail=$((fail + 1))
  printf 'FAIL %-18s %s\n' "$1" "$2"
}

check_file() {
  local label=$1 path=$2
  if [[ -r "$path" ]]; then
    ok "$label" "$path readable"
  else
    failure "$label" "$path not readable on this host"
  fi
}

echo "READ-ONLY competition demo preflight"
echo "NO outdoor or motor tests; no mode, arm, mission, network, or service changes"

if ((DRY_RUN)); then
  if [[ "$ROLE" == rdk || "$ROLE" == all ]]; then
    cat <<EOF
CHECK RDK/Tuya service: systemctl is-active $RDK_SERVICE
CHECK L610 default route: ip route get $TUYA_PROBE (prefer $L610_PREFERRED)
CHECK aircraft host route: ip route get $AIRCRAFT_HOST must use wlan0
CHECK RDK state snapshot: $RDK_STATE
EOF
  fi
  if [[ "$ROLE" == elf || "$ROLE" == all ]]; then
    cat <<EOF
CHECK ELF services: $AIRCRAFT_SERVICE and $VISION_SERVICE
CHECK optical state freshness without printing detailed telemetry
CHECK durable inbox and health snapshots: $ELF_INBOX $ELF_HEALTH
EOF
  fi
  cat <<EOF
RESULT dry-run only; no host state was changed
EOF
  exit 0
fi

if [[ "$ROLE" == rdk || "$ROLE" == all ]] &&
  command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet "$RDK_SERVICE"; then
    ok "RDK/Tuya" "$RDK_SERVICE active"
  else
    failure "RDK/Tuya" "$RDK_SERVICE inactive"
  fi
else
  warning "RDK/Tuya" "systemctl unavailable"
fi

if [[ "$ROLE" == rdk || "$ROLE" == all ]] &&
  command -v ip >/dev/null 2>&1; then
  cloud_route="$(ip route get "$TUYA_PROBE" 2>/dev/null | head -n1 || true)"
  cloud_iface="$(printf '%s\n' "$cloud_route" | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
  if [[ "$cloud_iface" == "$L610_PREFERRED" ||
    "$cloud_iface" == enx* || "$cloud_iface" == wwan* ||
    "$cloud_iface" == usb* ]]; then
    ok "L610" "Tuya probe route uses $cloud_iface"
  else
    failure "L610" "Tuya probe route is not on an ECM interface"
  fi

  aircraft_route="$(ip route get "$AIRCRAFT_HOST" 2>/dev/null | head -n1 || true)"
  if [[ "$aircraft_route" == *" dev wlan0"* ]]; then
    ok "aircraft route" "$AIRCRAFT_HOST uses wlan0"
  else
    failure "aircraft route" "$AIRCRAFT_HOST is not routed over wlan0"
  fi
else
  warning "routes" "ip command unavailable"
fi

if [[ "$ROLE" == elf || "$ROLE" == all ]] &&
  command -v systemctl >/dev/null 2>&1; then
  for service in "$AIRCRAFT_SERVICE" "$VISION_SERVICE"; do
    if systemctl is-active --quiet "$service"; then
      ok "ELF" "$service active"
    else
      failure "ELF" "$service inactive"
    fi
  done
fi

if [[ "$ROLE" == rdk || "$ROLE" == all ]]; then
  check_file "RDK snapshot" "$RDK_STATE"
fi
if [[ "$ROLE" == elf || "$ROLE" == all ]]; then
  check_file "ELF health" "$ELF_HEALTH"
  check_file "durable inbox" "$ELF_INBOX"
  check_file "optical state" "$OPTICAL_STATE"
fi

printf 'SUMMARY pass=%d warn=%d fail=%d\n' "$pass" "$warn" "$fail"
exit "$fail"
