#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate
GROUND_STATION_PORT="${GROUND_STATION_PORT:-8081}"

if [ "${SKIP_L610_CONFIG:-0}" != "1" ] && [ -x ./configure_l610_primary.sh ]; then
  ./configure_l610_primary.sh || true
fi

pkill -f "tuya_rover_agent.py run" 2>/dev/null || true
pkill -f "ground_station_server.py" 2>/dev/null || true

nohup python -u tuya_rover_agent.py run > rover_agent.log 2>&1 &
echo "$!" > rover_agent.pid

nohup python -u ground_station_server.py --host 0.0.0.0 --port "$GROUND_STATION_PORT" > ground_station.log 2>&1 &
echo "$!" > ground_station.pid

echo "rover agent pid: $(cat rover_agent.pid)"
echo "ground station pid: $(cat ground_station.pid)"
echo "ground station: http://$(hostname -I | awk '{print $1}'):$GROUND_STATION_PORT/"
