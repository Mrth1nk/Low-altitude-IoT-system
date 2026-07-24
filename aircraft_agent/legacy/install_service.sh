#!/bin/bash
set -e

cd "$(dirname "$0")"
sudo cp onboard_bridge.service /etc/systemd/system/onboard_bridge.service
sudo systemctl daemon-reload
sudo systemctl enable onboard_bridge.service
sudo systemctl restart onboard_bridge.service
sudo systemctl status onboard_bridge.service --no-pager
