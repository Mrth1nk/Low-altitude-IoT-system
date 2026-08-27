#!/usr/bin/env bash
set -euo pipefail

MODE=development
DRY_RUN=0
PERSIST=0
WIFI_IFACE="${RDK_WIFI_INTERFACE:-wlan0}"
SSID="${RDK_WIFI_SSID:-woshinailong}"
PROFILE="${RDK_WIFI_PROFILE:-low-altitude-aircraft}"
AIRCRAFT_HOST="${AIRCRAFT_HOST:-192.168.4.1}"
RDK_WIFI_ADDRESS="${RDK_WIFI_ADDRESS:-192.168.4.2/24}"
DEVELOPMENT_PROFILE="${RDK_DEVELOPMENT_PROFILE:-}"
AUTOCONNECT_PRIORITY="${RDK_WIFI_AUTOCONNECT_PRIORITY:-200}"

usage() {
  cat <<'EOF'
Usage: configure_rdk_network.sh [--dry-run] --mode development|demo|recover
       [--persist] [--development-profile NAME]

Passwords are accepted only through RDK_WIFI_PASSWORD_FILE (root:root, 0600)
or the calling process environment as RDK_WIFI_PASSWORD. They are never logged.
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --mode) MODE="${2:?missing mode}"; shift ;;
    --persist) PERSIST=1 ;;
    --development-profile) DEVELOPMENT_PROFILE="${2:?missing profile}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

case "$MODE" in development|demo|recover) ;; *) echo "invalid mode: $MODE" >&2; exit 2 ;; esac

print_cmd() {
  printf '+'
  printf ' %s' "$@"
  printf '\n'
}

run() {
  if ((DRY_RUN)); then print_cmd "$@"; else "$@"; fi
}

read_password() {
  local owner mode
  if [[ -n "${RDK_WIFI_PASSWORD_FILE:-}" ]]; then
    [[ -f "$RDK_WIFI_PASSWORD_FILE" ]] || { echo "password file not found" >&2; return 1; }
    owner="$(stat -c %u "$RDK_WIFI_PASSWORD_FILE")"
    mode="$(stat -c %a "$RDK_WIFI_PASSWORD_FILE")"
    [[ "$owner" == 0 && "$mode" == 600 ]] || {
      echo "password file must be owned by root with mode 0600" >&2
      return 1
    }
    IFS= read -r WIFI_PASSWORD < "$RDK_WIFI_PASSWORD_FILE"
  else
    WIFI_PASSWORD="${RDK_WIFI_PASSWORD:-}"
  fi
  [[ -n "$WIFI_PASSWORD" ]] || { echo "demo mode requires a protected Wi-Fi password" >&2; return 1; }
}

if [[ "$MODE" == recover ]]; then
  [[ -n "$DEVELOPMENT_PROFILE" ]] || {
    echo "--development-profile is required for recovery" >&2
    exit 2
  }
  run nmcli connection down "$PROFILE"
  run nmcli connection up "$DEVELOPMENT_PROFILE" ifname "$WIFI_IFACE"
  exit 0
fi

autoconnect=no
[[ "$MODE" == demo ]] && ((PERSIST)) && autoconnect=yes

if ((DRY_RUN)); then
  WIFI_PASSWORD="${RDK_WIFI_PASSWORD:+set}"
  [[ -n "${RDK_WIFI_PASSWORD_FILE:-}" ]] && WIFI_PASSWORD=set
  [[ "$MODE" == development ]] || [[ -n "$WIFI_PASSWORD" ]] || WIFI_PASSWORD=set
else
  command -v nmcli >/dev/null
  [[ "$(id -u)" == 0 ]] || { echo "network configuration requires root" >&2; exit 1; }
  [[ "$MODE" == development ]] || read_password
fi

if ((DRY_RUN)); then
  print_cmd nmcli connection add type wifi ifname "$WIFI_IFACE" con-name "$PROFILE" ssid "$SSID"
else
  nmcli -t -f NAME connection show | grep -Fxq "$PROFILE" ||
    nmcli connection add type wifi ifname "$WIFI_IFACE" con-name "$PROFILE" ssid "$SSID"
fi

run nmcli connection modify "$PROFILE" \
  802-11-wireless.ssid "$SSID" \
  802-11-wireless.mode infrastructure \
  ipv4.method manual \
  ipv4.addresses "$RDK_WIFI_ADDRESS" \
  ipv4.gateway "" \
  ipv4.dns "" \
  ipv4.never-default yes \
  ipv4.routes "$AIRCRAFT_HOST/32" \
  ipv6.never-default yes \
  connection.autoconnect "$autoconnect" \
  connection.autoconnect-priority "$AUTOCONNECT_PRIORITY"

if [[ "$MODE" != development ]]; then
  if ((DRY_RUN)); then
    print_cmd nmcli connection modify "$PROFILE" wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk '<redacted>'
  else
    # Supply the secret separately so it is never included in diagnostic output.
    nmcli connection modify "$PROFILE" wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "$WIFI_PASSWORD" >/dev/null
    unset WIFI_PASSWORD
  fi
fi

if [[ "$MODE" == demo ]]; then
  run nmcli connection up "$PROFILE" ifname "$WIFI_IFACE"
  run ip route replace "$AIRCRAFT_HOST/32" dev "$WIFI_IFACE"
else
  echo "development mode: demo profile prepared with autoconnect no; current Wi-Fi remains active"
fi
