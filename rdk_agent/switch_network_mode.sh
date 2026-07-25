#!/usr/bin/env bash
set -euo pipefail

REQUEST_PATH="${NETWORK_MODE_REQUEST_PATH:-/run/low-altitude-iot/network-mode}"
PHONE_PROFILE="${PHONE_WIFI_PROFILE:-Mr.think的Mate 70 Pro+}"
AIRCRAFT_PROFILE="${AIRCRAFT_WIFI_PROFILE:-low-altitude-aircraft}"
WIFI_IFACE="${WIFI_IFACE:-wlan0}"

mode="$(tr -d '\r\n' < "$REQUEST_PATH")"
rm -f "$REQUEST_PATH"
sleep "${NETWORK_SWITCH_DELAY_SEC:-3}"

configure_profile() {
  nmcli con mod "$1" \
    connection.autoconnect yes \
    ipv4.never-default yes ipv4.route-metric 950 \
    ipv6.never-default yes ipv6.route-metric 950
}

case "$mode" in
  phone)
    configure_profile "$PHONE_PROFILE"
    nmcli con up "$PHONE_PROFILE" ifname "$WIFI_IFACE"
    ;;
  aircraft)
    configure_profile "$AIRCRAFT_PROFILE"
    if ! nmcli con up "$AIRCRAFT_PROFILE" ifname "$WIFI_IFACE"; then
      configure_profile "$PHONE_PROFILE"
      nmcli con up "$PHONE_PROFILE" ifname "$WIFI_IFACE"
      exit 1
    fi
    ip route replace 192.168.4.1 dev "$WIFI_IFACE" scope link
    ;;
  *)
    echo "unsupported network mode request" >&2
    exit 2
    ;;
esac

nmcli -t -f DEVICE,STATE,CONNECTION dev status
ip -br addr show dev "$WIFI_IFACE"

# Recreate the Tuya MQTT session after NetworkManager changes link state.
# A half-open TLS socket can otherwise keep accepting local publishes while
# the cloud shadow no longer advances.
sleep "${MQTT_RECONNECT_DELAY_SEC:-1}"
systemctl try-restart low-altitude-rdk.service
