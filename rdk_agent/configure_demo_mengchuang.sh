#!/usr/bin/env bash
set -euo pipefail

SSID="${MENGCHUANG_SSID:-mengchuang}"
PASSWORD="${MENGCHUANG_PASSWORD:-mengchuang}"
PHONE_CONNECTION="${PHONE_CONNECTION:-Mr.think的Mate 70 Pro+}"
LOG="${MENGCHUANG_DEMO_LOG:-/tmp/mengchuang-demo.log}"

{
  echo "=== $(date '+%F %T') configure demo Wi-Fi ==="
  nmcli dev wifi rescan ifname wlan0 || true
  sleep 3
  nmcli -t -f SSID,IN-USE,SIGNAL,SECURITY dev wifi list ifname wlan0 || true

  if ! nmcli -t -f SSID dev wifi list ifname wlan0 | grep -Fxq "${SSID}"; then
    echo "ERROR: ${SSID} not found by wlan0 scan"
    exit 2
  fi

  nmcli con show "${SSID}" >/dev/null 2>&1 || nmcli dev wifi connect "${SSID}" password "${PASSWORD}" ifname wlan0
  sudo nmcli con mod "${SSID}" connection.autoconnect yes ipv4.never-default yes ipv4.route-metric 950 ipv6.never-default yes ipv6.route-metric 950
  sudo nmcli con mod "${PHONE_CONNECTION}" connection.autoconnect yes ipv4.never-default yes ipv4.route-metric 900 ipv6.never-default yes ipv6.route-metric 900 || true
  sudo nmcli con up "${SSID}" ifname wlan0

  echo "after:"
  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status || true
  ip -br addr wlan0 || true
  ip route || true
  echo "=== $(date '+%F %T') demo Wi-Fi configured ==="
} | tee -a "${LOG}"
