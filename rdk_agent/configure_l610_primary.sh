#!/usr/bin/env bash
set -euo pipefail

L610_CONNECTION="${L610_CONNECTION:-Wired connection 1}"
PHONE_CONNECTION="${PHONE_CONNECTION:-Mr.think的Mate 70 Pro+}"
MENGCHUANG_CONNECTION="${MENGCHUANG_CONNECTION:-woshinailong}"
MENGCHUANG_IFACE="${MENGCHUANG_IFACE:-wlan0}"
MENGCHUANG_MODULE_IP="${MENGCHUANG_MODULE_IP:-192.168.4.1}"
LOG="${L610_PRIMARY_LOG:-/tmp/l610-primary.log}"

{
  echo "=== $(date '+%F %T') configure L610 primary route ==="

  sudo modprobe usbnet || true
  sudo modprobe cdc_ether || true
  sleep 2

  if nmcli con show "${L610_CONNECTION}" >/dev/null 2>&1; then
    sudo nmcli con mod "${L610_CONNECTION}" connection.autoconnect yes ipv4.never-default no ipv4.route-metric 100 ipv6.method ignore || true
    sudo nmcli con up "${L610_CONNECTION}" || true
  fi

  if nmcli con show "${PHONE_CONNECTION}" >/dev/null 2>&1; then
    sudo nmcli con mod "${PHONE_CONNECTION}" connection.autoconnect yes ipv4.never-default yes ipv4.route-metric 900 ipv6.never-default yes ipv6.route-metric 900 || true
  fi

  if nmcli con show "${MENGCHUANG_CONNECTION}" >/dev/null 2>&1; then
    sudo nmcli con mod "${MENGCHUANG_CONNECTION}" connection.autoconnect no ipv4.never-default yes ipv4.route-metric 950 ipv6.never-default yes ipv6.route-metric 950 || true
  fi

  if nmcli con show "${L610_CONNECTION}" >/dev/null 2>&1; then
    sudo nmcli con up "${L610_CONNECTION}" || true
  fi

  active_l610_iface="$(nmcli -t -f DEVICE,CONNECTION dev status | awk -F: -v con="${L610_CONNECTION}" '$2 == con {print $1; exit}')"
  if [ -n "${active_l610_iface}" ]; then
    while read -r line; do
      set -- ${line}
      gateway="$3"
      iface="$5"
      if [ "${iface}" != "${active_l610_iface}" ]; then
        sudo ip route del default via "${gateway}" dev "${iface}" 2>/dev/null || true
      fi
    done < <(ip route show default || true)
  fi

  if ip link show "${MENGCHUANG_IFACE}" >/dev/null 2>&1; then
    sudo ip route replace "${MENGCHUANG_MODULE_IP}" dev "${MENGCHUANG_IFACE}" scope link || true
  fi

  printf "usbnet\ncdc_ether\n" | sudo tee /etc/modules-load.d/l610-cdc-ether.conf >/dev/null

  echo "devices:"
  nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status || true
  echo "addresses:"
  ip -br addr || true
  echo "routes:"
  ip route || true
  echo "tuya route:"
  ip route get 139.196.6.123 || true
  echo "=== $(date '+%F %T') L610 primary route configured ==="
} | tee -a "${LOG}"
