#!/usr/bin/env bash
set -u

LOG=/tmp/rdk-aircraft-link-diagnostic.log
PHONE_PROFILE="${PHONE_WIFI_PROFILE:-Mr.think的Mate 70 Pro+}"
AIRCRAFT_PROFILE="${AIRCRAFT_WIFI_PROFILE:-low-altitude-aircraft}"
WIFI_IFACE="${WIFI_IFACE:-wlan0}"

exec >"$LOG" 2>&1
date

restore_phone() {
  nmcli --wait 15 connection up "$PHONE_PROFILE" ifname "$WIFI_IFACE" || true
  date
  nmcli -t -f DEVICE,STATE,CONNECTION device status || true
}
trap restore_phone EXIT

nmcli --wait 15 connection up "$AIRCRAFT_PROFILE" ifname "$WIFI_IFACE"
sleep 2
nmcli -t -f DEVICE,STATE,CONNECTION device status
ip -br address show dev "$WIFI_IFACE"
ip route
ip neighbor show dev "$WIFI_IFACE"
ss -lunp | grep 14560 || true
timeout 12 tcpdump -ni "$WIFI_IFACE" -c 30 'udp port 14555 or udp port 14560' || true
ip -s link show dev "$WIFI_IFACE"
