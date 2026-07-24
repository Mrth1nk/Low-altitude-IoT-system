#!/usr/bin/env bash
set -euo pipefail

SSID="${MENGCHUANG_SSID:-mengchuang}"
PASSWORD="${MENGCHUANG_PASSWORD:-mengchuang}"
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
  nmcli dev wifi connect "${SSID}" password "${PASSWORD}" ifname wlan0
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
