#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == --dry-run ]] && DRY_RUN=1
WIFI_IFACE="${RDK_WIFI_INTERFACE:-wlan0}"
AIRCRAFT_HOST="${AIRCRAFT_HOST:-192.168.4.1}"
TUYA_ROUTE_PROBE="${TUYA_ROUTE_PROBE:-139.196.6.123}"
PREFERRED_L610="${L610_INTERFACE:-enxf04bb3b9ebe5}"

if ((DRY_RUN)); then
  cat <<EOF
CHECK base service low-altitude-rdk.service is active
CHECK L610 interface preferred=$PREFERRED_L610; detect current ECM/default-route interface
CHECK ip route get $TUYA_ROUTE_PROBE uses L610 interface and not $WIFI_IFACE
CHECK ip route get $AIRCRAFT_HOST uses $WIFI_IFACE
WARN aircraft link is optional; its failure must not fail Rover/Tuya health
EOF
  exit 0
fi

fail=0
systemctl is-active --quiet low-altitude-rdk.service || {
  echo "FAIL: Rover/Tuya service is not active" >&2
  fail=1
}

cloud_route="$(ip route get "$TUYA_ROUTE_PROBE" 2>/dev/null | head -n1 || true)"
route_iface="$(printf '%s\n' "$cloud_route" | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
l610_iface="$route_iface"
if [[ "$route_iface" == "$PREFERRED_L610" ]]; then
  l610_iface="$PREFERRED_L610"
elif [[ "$route_iface" != enx* && "$route_iface" != wwan* &&
  "$route_iface" != usb* ]]; then
  l610_iface=
fi
if [[ -z "$cloud_route" || -z "$l610_iface" || "$l610_iface" == "$WIFI_IFACE" ]]; then
  echo "FAIL: Tuya route is not using an L610/current ECM interface" >&2
  fail=1
else
  echo "OK: Tuya route uses $l610_iface"
fi

aircraft_route="$(ip route get "$AIRCRAFT_HOST" 2>/dev/null | head -n1 || true)"
if [[ "$aircraft_route" == *" dev $WIFI_IFACE"* ]]; then
  echo "OK: aircraft host route uses $WIFI_IFACE"
else
  echo "WARN: aircraft link unavailable; Rover/Tuya remain operational" >&2
fi

exit "$fail"
