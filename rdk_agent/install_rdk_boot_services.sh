#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RDK_DIR="$REPO_ROOT/rdk_agent"

if [ ! -f "$REPO_ROOT/shared_protocol/__init__.py" ] || [ ! -x "$RDK_DIR/start_rover_stack.sh" ]; then
  echo "error: full repository layout required at $REPO_ROOT (expected sibling rdk_agent and shared_protocol)" >&2
  exit 2
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
  render_rover_service
  exit 0
fi

sudo install -m 0755 "${RDK_DIR}/configure_l610_primary.sh" /usr/local/sbin/uav-configure-l610-primary

sudo tee /etc/systemd/system/uav-l610-primary.service >/dev/null <<'EOF'
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

render_rover_service | sudo tee /etc/systemd/system/uav-rover-stack.service >/dev/null

sudo systemctl daemon-reload
sudo systemctl disable --now uav-mengchuang-link.service 2>/dev/null || true
sudo systemctl enable uav-l610-primary.service
sudo systemctl enable uav-rover-stack.service
sudo systemctl restart uav-l610-primary.service
sudo systemctl restart uav-rover-stack.service

systemctl --no-pager --full status uav-l610-primary.service || true
systemctl --no-pager --full status uav-rover-stack.service || true
ip route || true
ip route get 139.196.6.123 || true
