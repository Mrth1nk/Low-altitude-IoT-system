#!/usr/bin/env bash
set -euo pipefail

SSID="${MENGCHUANG_SSID:-woshinailong}"
PASSWORD="${MENGCHUANG_PASSWORD:-}"
RESTORE_CONNECTION="${RESTORE_CONNECTION:-Mr.think的Mate 70 Pro+}"
HOLD_SECONDS="${1:-60}"
LOG="${MENGCHUANG_TEST_LOG:-/tmp/mengchuang-test.log}"

{
  echo "=== $(date '+%F %T') mengchuang test start ==="
  echo "before:"
  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status || true
  ip -br addr wlan0 || true
  echo "scan:"
  nmcli dev wifi rescan ifname wlan0 || true
  sleep 3
  nmcli -t -f SSID,IN-USE,SIGNAL,SECURITY dev wifi list ifname wlan0 || true
  echo "connect ${SSID}"
  if nmcli con show "${SSID}" >/dev/null 2>&1; then
    nmcli con mod "${SSID}" ipv4.never-default yes ipv6.never-default yes
    nmcli con up "${SSID}" ifname wlan0
  else
    if [ -z "${PASSWORD}" ]; then
      echo "MENGCHUANG_PASSWORD is required when NetworkManager has no saved ${SSID} profile" >&2
      exit 2
    fi
    nmcli dev wifi connect "${SSID}" password "${PASSWORD}" ifname wlan0
    nmcli con mod "${SSID}" ipv4.never-default yes ipv6.never-default yes
  fi
  echo "connected:"
  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status || true
  ip -br addr wlan0 || true
  ip route || true
  echo "hold ${HOLD_SECONDS}s for aircraft telemetry capture"
  sleep "${HOLD_SECONDS}"
  echo "restore ${RESTORE_CONNECTION}"
  nmcli con up "${RESTORE_CONNECTION}" ifname wlan0
  echo "after restore:"
  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status || true
  ip -br addr wlan0 || true
  echo "=== $(date '+%F %T') mengchuang test done ==="
} >>"${LOG}" 2>&1
