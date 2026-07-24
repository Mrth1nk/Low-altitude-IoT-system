#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
if [ ! -f "$REPO_ROOT/shared_protocol/__init__.py" ] || [ ! -d "$REPO_ROOT/rdk_agent" ]; then
  echo "error: full repository layout required at $REPO_ROOT (expected sibling rdk_agent and shared_protocol)" >&2
  exit 2
fi

MENGCHUANG_CONNECTION="${MENGCHUANG_CONNECTION:-woshinailong}"
MENGCHUANG_IFACE="${MENGCHUANG_IFACE:-wlan0}"
INSTALL_ROOT="${INSTALL_ROOT:-}"
CONNECT_TARGET="${INSTALL_ROOT}/usr/local/sbin/uav-connect-aircraft-wifi"
UNIT_TARGET="${INSTALL_ROOT}/etc/systemd/system/uav-mengchuang-link.service"
PYTHON_BIN="${PYTHON_BIN:-$REPO_ROOT/rdk_agent/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_FALLBACK:-python3}"
fi

PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -c \
  "import shared_protocol; import rdk_agent.aircraft_link; import rdk_agent.aircraft_transport"

render_connect_script() {
  cat <<EOF
#!/usr/bin/env bash
set -euo pipefail
nmcli con mod "${MENGCHUANG_CONNECTION}" connection.autoconnect yes ipv4.never-default yes ipv4.route-metric 950 ipv6.never-default yes ipv6.route-metric 950
nmcli con up "${MENGCHUANG_CONNECTION}" ifname "${MENGCHUANG_IFACE}"
ip route replace 192.168.4.1 dev "${MENGCHUANG_IFACE}" scope link
EOF
}

render_unit() {
  cat <<EOF
[Unit]
Description=Connect RDK Wi-Fi to aircraft telemetry link
After=NetworkManager.service uav-l610-primary.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=${CONNECT_TARGET}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
}

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "preflight imports ok"
  echo "backup existing units/scripts"
  echo "rollback on failure"
  echo "health-check services and active NetworkManager connection"
  echo "record exact active WiFi profile"
  echo "down woshinailong on rollback"
  echo "restore prior active WiFi profile on rollback"
  render_connect_script
  render_unit
  exit 0
fi

BACKUP_DIR="$(mktemp -d /tmp/low-altitude-wifi-install.XXXXXX)"
CONNECT_EXISTED=0
UNIT_EXISTED=0
UNIT_WAS_ENABLED=0
UNIT_WAS_ACTIVE=0
PRIOR_WIFI_PROFILE="$(
  nmcli -t -f NAME,DEVICE con show --active 2>/dev/null |
    awk -F: -v iface="$MENGCHUANG_IFACE" '$2 == iface {print $1; exit}'
)"
if sudo test -e "$CONNECT_TARGET"; then
  sudo cp -a "$CONNECT_TARGET" "$BACKUP_DIR/connect"
  CONNECT_EXISTED=1
fi
if sudo test -e "$UNIT_TARGET"; then
  sudo cp -a "$UNIT_TARGET" "$BACKUP_DIR/unit"
  UNIT_EXISTED=1
fi
if systemctl is-enabled --quiet uav-mengchuang-link.service 2>/dev/null; then
  UNIT_WAS_ENABLED=1
fi
if systemctl is-active --quiet uav-mengchuang-link.service 2>/dev/null; then
  UNIT_WAS_ACTIVE=1
fi

rollback() {
  status=$?
  set +e
  nmcli con down "$MENGCHUANG_CONNECTION" 2>/dev/null || true
  if [ -n "$PRIOR_WIFI_PROFILE" ]; then
    nmcli con up "$PRIOR_WIFI_PROFILE" ifname "$MENGCHUANG_IFACE" || true
  fi
  if [ "$CONNECT_EXISTED" = "1" ]; then
    sudo cp -a "$BACKUP_DIR/connect" "$CONNECT_TARGET"
  else
    sudo rm -f "$CONNECT_TARGET"
  fi
  if [ "$UNIT_EXISTED" = "1" ]; then
    sudo cp -a "$BACKUP_DIR/unit" "$UNIT_TARGET"
  else
    sudo rm -f "$UNIT_TARGET"
  fi
  sudo systemctl daemon-reload
  if [ "$UNIT_WAS_ENABLED" = "1" ]; then
    sudo systemctl enable uav-mengchuang-link.service
  else
    sudo systemctl disable uav-mengchuang-link.service
  fi
  if [ "$UNIT_WAS_ACTIVE" = "1" ]; then
    sudo systemctl restart uav-mengchuang-link.service
  else
    sudo systemctl stop uav-mengchuang-link.service
  fi
  echo "installation failed; previous Wi-Fi service restored" >&2
  exit "$status"
}
trap rollback ERR

render_connect_script | sudo tee "$CONNECT_TARGET" >/dev/null
sudo chmod 0755 "$CONNECT_TARGET"
render_unit | sudo tee "$UNIT_TARGET" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable uav-mengchuang-link.service
sudo systemctl restart uav-mengchuang-link.service
sudo systemctl is-active --quiet uav-mengchuang-link.service
nmcli -t -f NAME con show --active | grep -Fxq "$MENGCHUANG_CONNECTION"

trap - ERR
rm -rf "$BACKUP_DIR"
systemctl --no-pager --full status uav-mengchuang-link.service
ip -br addr "${MENGCHUANG_IFACE}" || true
ip route || true
