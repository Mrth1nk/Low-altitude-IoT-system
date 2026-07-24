#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SWITCH_TARGET="/usr/local/sbin/low-altitude-switch-network"
SERVICE_TARGET="/etc/systemd/system/low-altitude-network-switch.service"
PATH_TARGET="/etc/systemd/system/low-altitude-network-switch.path"
TMPFILES_TARGET="/etc/tmpfiles.d/low-altitude-network-switch.conf"

sudo install -m 0755 "$SCRIPT_DIR/switch_network_mode.sh" "$SWITCH_TARGET"
printf '%s\n' 'd /run/low-altitude-iot 0770 root sunrise -' |
  sudo tee "$TMPFILES_TARGET" >/dev/null

sudo tee "$SERVICE_TARGET" >/dev/null <<'EOF'
[Unit]
Description=Apply a validated RDK Wi-Fi mode request
After=NetworkManager.service

[Service]
Type=oneshot
Environment="PHONE_WIFI_PROFILE=Mr.think的Mate 70 Pro+"
Environment=AIRCRAFT_WIFI_PROFILE=low-altitude-aircraft
Environment=WIFI_IFACE=wlan0
ExecStart=/usr/local/sbin/low-altitude-switch-network
EOF

sudo tee "$PATH_TARGET" >/dev/null <<'EOF'
[Unit]
Description=Watch for validated RDK Wi-Fi mode requests
After=NetworkManager.service

[Path]
PathExists=/run/low-altitude-iot/network-mode
Unit=low-altitude-network-switch.service

[Install]
WantedBy=multi-user.target
EOF

sudo systemd-tmpfiles --create "$TMPFILES_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable --now low-altitude-network-switch.path
sudo systemctl is-active --quiet low-altitude-network-switch.path
