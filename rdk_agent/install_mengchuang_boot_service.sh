#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
if [ ! -f "$REPO_ROOT/shared_protocol/__init__.py" ] || [ ! -d "$REPO_ROOT/rdk_agent" ]; then
  echo "error: full repository layout required at $REPO_ROOT (expected sibling rdk_agent and shared_protocol)" >&2
  exit 2
fi

MENGCHUANG_CONNECTION="${MENGCHUANG_CONNECTION:-mengchuang}"
MENGCHUANG_IFACE="${MENGCHUANG_IFACE:-wlan0}"

sudo tee /usr/local/sbin/uav-connect-mengchuang >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail
nmcli con mod "${MENGCHUANG_CONNECTION}" connection.autoconnect yes ipv4.never-default yes ipv4.route-metric 950 ipv6.never-default yes ipv6.route-metric 950 || true
nmcli con up "${MENGCHUANG_CONNECTION}" ifname "${MENGCHUANG_IFACE}" || true
ip route replace 192.168.4.1 dev "${MENGCHUANG_IFACE}" scope link || true
EOF
sudo chmod +x /usr/local/sbin/uav-connect-mengchuang

sudo tee /etc/systemd/system/uav-mengchuang-link.service >/dev/null <<'EOF'
[Unit]
Description=Connect RDK Wi-Fi to mengchuang telemetry link
After=NetworkManager.service uav-l610-primary.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/uav-connect-mengchuang
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable uav-mengchuang-link.service
sudo systemctl restart uav-mengchuang-link.service
systemctl --no-pager --full status uav-mengchuang-link.service || true
ip -br addr "${MENGCHUANG_IFACE}" || true
ip route || true
