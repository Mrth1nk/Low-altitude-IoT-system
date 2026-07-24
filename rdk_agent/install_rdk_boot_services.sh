#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RDK_DIR="$REPO_ROOT/rdk_agent"
INSTALL_ROOT="${INSTALL_ROOT:-}"
RDK_ENV_FILE="${RDK_ENV_FILE:-/etc/low-altitude-iot/rdk.env}"
ENV_FILE_TARGET="${INSTALL_ROOT}${RDK_ENV_FILE}"

if [ ! -f "$REPO_ROOT/shared_protocol/__init__.py" ] || [ ! -x "$RDK_DIR/start_rover_stack.sh" ]; then
  echo "error: full repository layout required at $REPO_ROOT (expected sibling rdk_agent and shared_protocol)" >&2
  exit 2
fi

PYTHON_BIN="${PYTHON_BIN:-$RDK_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_FALLBACK:-python3}"
fi
PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" -c \
  "import shared_protocol; import rdk_agent.aircraft_link; import rdk_agent.aircraft_transport"

PSK_VALUE="${AIRCRAFT_LINK_PSK:-}"
PSK_FROM_ENV=0
if [ -n "$PSK_VALUE" ]; then
  PSK_FROM_ENV=1
elif sudo test -r "$ENV_FILE_TARGET"; then
  PSK_VALUE="$(
    sudo awk -F= '
      $1 == "AIRCRAFT_LINK_PSK" {
        sub(/^[^=]*=/, "")
        print
        exit
      }
    ' "$ENV_FILE_TARGET"
  )"
fi
PSK_BYTES="$(LC_ALL=C printf '%s' "$PSK_VALUE" | wc -c | tr -d '[:space:]')"
if [ "$PSK_BYTES" -lt 16 ] ||
  [[ "$PSK_VALUE" == REPLACE_* ]] ||
  [[ "$PSK_VALUE" == *$'\n'* ]] ||
  [[ "$PSK_VALUE" == *$'\r'* ]]; then
  echo "error: AIRCRAFT_LINK_PSK must be a single-line value of at least 16 bytes in the environment or $RDK_ENV_FILE" >&2
  exit 3
fi

render_rover_service() {
  cat <<EOF
[Unit]
Description=UAV Tuya rover agent and ground station
After=NetworkManager.service uav-l610-primary.service
Wants=NetworkManager.service uav-l610-primary.service

[Service]
Type=forking
User=sunrise
Group=sunrise
Environment=HOME=/home/sunrise
Environment=SKIP_L610_CONFIG=1
Environment=PYTHONPATH=${REPO_ROOT}
EnvironmentFile=${RDK_ENV_FILE}
WorkingDirectory=${REPO_ROOT}
ExecStart=${RDK_DIR}/start_rover_stack.sh
PIDFile=${RDK_DIR}/rover_agent.pid
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "preflight imports ok"
  echo "backup existing units/scripts"
  echo "rollback on failure"
  echo "health-check services"
  echo "AIRCRAFT_LINK_PSK=<redacted>"
  render_rover_service
  exit 0
fi

CONFIG_TARGET="${INSTALL_ROOT}/usr/local/sbin/uav-configure-l610-primary"
L610_UNIT="${INSTALL_ROOT}/etc/systemd/system/uav-l610-primary.service"
ROVER_UNIT="${INSTALL_ROOT}/etc/systemd/system/uav-rover-stack.service"
BACKUP_DIR="$(mktemp -d /tmp/low-altitude-rdk-install.XXXXXX)"
CONFIG_EXISTED=0
L610_EXISTED=0
ROVER_EXISTED=0
ENV_FILE_EXISTED=0
L610_WAS_ENABLED=0
L610_WAS_ACTIVE=0
ROVER_WAS_ENABLED=0
ROVER_WAS_ACTIVE=0
MENGCHUANG_WAS_ENABLED=0
MENGCHUANG_WAS_ACTIVE=0
if sudo test -e "$CONFIG_TARGET"; then
  sudo cp -a "$CONFIG_TARGET" "$BACKUP_DIR/configure"
  CONFIG_EXISTED=1
fi
if sudo test -e "$L610_UNIT"; then
  sudo cp -a "$L610_UNIT" "$BACKUP_DIR/l610-unit"
  L610_EXISTED=1
fi
if sudo test -e "$ROVER_UNIT"; then
  sudo cp -a "$ROVER_UNIT" "$BACKUP_DIR/rover-unit"
  ROVER_EXISTED=1
fi
if sudo test -e "$ENV_FILE_TARGET"; then
  sudo cp -a "$ENV_FILE_TARGET" "$BACKUP_DIR/rdk-env"
  ENV_FILE_EXISTED=1
fi
if systemctl is-enabled --quiet uav-l610-primary.service 2>/dev/null; then
  L610_WAS_ENABLED=1
fi
if systemctl is-active --quiet uav-l610-primary.service 2>/dev/null; then
  L610_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet uav-rover-stack.service 2>/dev/null; then
  ROVER_WAS_ENABLED=1
fi
if systemctl is-active --quiet uav-rover-stack.service 2>/dev/null; then
  ROVER_WAS_ACTIVE=1
fi
if systemctl is-enabled --quiet uav-mengchuang-link.service 2>/dev/null; then
  MENGCHUANG_WAS_ENABLED=1
fi
if systemctl is-active --quiet uav-mengchuang-link.service 2>/dev/null; then
  MENGCHUANG_WAS_ACTIVE=1
fi

rollback() {
  status=$?
  set +e
  for spec in \
    "$CONFIG_EXISTED:$BACKUP_DIR/configure:$CONFIG_TARGET" \
    "$L610_EXISTED:$BACKUP_DIR/l610-unit:$L610_UNIT" \
    "$ROVER_EXISTED:$BACKUP_DIR/rover-unit:$ROVER_UNIT"; do
    existed="${spec%%:*}"
    rest="${spec#*:}"
    backup="${rest%%:*}"
    target="${rest#*:}"
    if [ "$existed" = "1" ]; then
      sudo cp -a "$backup" "$target"
    else
      sudo rm -f "$target"
    fi
  done
  if [ "$ENV_FILE_EXISTED" = "1" ]; then
    sudo cp -a "$BACKUP_DIR/rdk-env" "$ENV_FILE_TARGET"
  else
    sudo rm -f "$ENV_FILE_TARGET"
  fi
  sudo systemctl daemon-reload
  if [ "$L610_WAS_ENABLED" = "1" ]; then
    sudo systemctl enable uav-l610-primary.service
  else
    sudo systemctl disable uav-l610-primary.service
  fi
  if [ "$L610_WAS_ACTIVE" = "1" ]; then
    sudo systemctl restart uav-l610-primary.service
  else
    sudo systemctl stop uav-l610-primary.service
  fi
  if [ "$ROVER_WAS_ENABLED" = "1" ]; then
    sudo systemctl enable uav-rover-stack.service
  else
    sudo systemctl disable uav-rover-stack.service
  fi
  if [ "$ROVER_WAS_ACTIVE" = "1" ]; then
    sudo systemctl restart uav-rover-stack.service
  else
    sudo systemctl stop uav-rover-stack.service
  fi
  if [ "$MENGCHUANG_WAS_ENABLED" = "1" ]; then
    sudo systemctl enable uav-mengchuang-link.service
  else
    sudo systemctl disable uav-mengchuang-link.service
  fi
  if [ "$MENGCHUANG_WAS_ACTIVE" = "1" ]; then
    sudo systemctl restart uav-mengchuang-link.service
  else
    sudo systemctl stop uav-mengchuang-link.service
  fi
  echo "installation failed; previous RDK services restored" >&2
  rm -rf "$BACKUP_DIR"
  exit "$status"
}
trap rollback ERR

sudo install -d -m 0700 "$(dirname "$ENV_FILE_TARGET")"
if [ "$PSK_FROM_ENV" = "1" ]; then
  printf 'AIRCRAFT_LINK_PSK=%s\n' "$PSK_VALUE" |
    sudo tee "$ENV_FILE_TARGET" >/dev/null
fi
sudo chmod 0600 "$ENV_FILE_TARGET"
sudo chown root:root "$ENV_FILE_TARGET"
sudo install -m 0755 "${RDK_DIR}/configure_l610_primary.sh" "$CONFIG_TARGET"

sudo tee "$L610_UNIT" >/dev/null <<'EOF'
[Unit]
Description=Keep UAV Tuya traffic on Fibocom L610 and Wi-Fi telemetry local
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/uav-configure-l610-primary
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

render_rover_service | sudo tee "$ROVER_UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl disable --now uav-mengchuang-link.service 2>/dev/null || true
sudo systemctl enable uav-l610-primary.service
sudo systemctl enable uav-rover-stack.service
sudo systemctl restart uav-l610-primary.service
sudo systemctl restart uav-rover-stack.service
sudo systemctl is-active --quiet uav-l610-primary.service
sudo systemctl is-active --quiet uav-rover-stack.service

trap - ERR
rm -rf "$BACKUP_DIR"
systemctl --no-pager --full status uav-l610-primary.service
systemctl --no-pager --full status uav-rover-stack.service
ip route || true
ip route get 139.196.6.123 || true
