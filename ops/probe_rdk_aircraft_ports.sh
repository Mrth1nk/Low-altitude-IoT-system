#!/usr/bin/env bash
set -u

LOG=/tmp/rdk-aircraft-port-probe.log
PHONE_PROFILE="${PHONE_WIFI_PROFILE:-Mr.think的Mate 70 Pro+}"
AIRCRAFT_PROFILE="${AIRCRAFT_WIFI_PROFILE:-low-altitude-aircraft}"
WIFI_IFACE="${WIFI_IFACE:-wlan0}"

exec >"$LOG" 2>&1
restore_phone() {
  nmcli --wait 15 connection up "$PHONE_PROFILE" ifname "$WIFI_IFACE" || true
}
trap restore_phone EXIT

nmcli --wait 15 connection up "$AIRCRAFT_PROFILE" ifname "$WIFI_IFACE"
sleep 2
for port in 14550 14555 14560 8080 8899; do
  printf 'LIOT_PORT_TEST_%s\n' "$port" |
    timeout 2 nc -u -w 1 192.168.4.1 "$port" || true
  sleep 1
done
