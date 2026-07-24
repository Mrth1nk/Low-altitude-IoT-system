# AGENTS.md

## Project Overview

This workspace contains the competition IoT rover stack built around:

- RDK X5 as the rover-side main controller.
- Fibocom L610 as the cellular uplink to TuyaLink.
- TuyaLink / Tuya Cloud as the cloud telemetry and command channel.
- ArduPilot Rover flight controller for car motion control.
- `mengchuang` serial Wi-Fi telemetry link for aircraft MAVLink messages between the aircraft board and RDK.
- Browser ground stations for local RDK debugging and Tuya-cloud operation.

The current operational goal is: keep RDK controllable over SSH during development, keep cloud traffic on L610 during demos, and only switch RDK Wi-Fi to `mengchuang` at the final aircraft-link demo step.

## Directory Structure

- `uav_tuya_agent/`: RDK-side Python rover agent, local web ground station, L610 network scripts, aircraft Wi-Fi telemetry gateway, and deployment scripts.
- `uav_tuya_agent/web/`: RDK local dashboard served by `ground_station_server.py` on port `8080`.
- `uav_tuya_agent/tests/`: Python tests for command models, Tuya auth, L610 RSSI parsing, MAVLink Rover helpers, aircraft gateway, and aircraft command packets.
- `tuya_cloud_ground_station/`: Local computer web station that reads Tuya Cloud OpenAPI state and sends Tuya property commands.
- `tuya_cloud_ground_station/public/`: Browser UI for the Tuya cloud ground station.
- `docs/superpowers/`: Earlier design and implementation plan notes.
- `docs/codex_context.md`: Condensed operational context from the Codex session.
- `wifi_telemetry_gateway.py` and `aircraft_status_server.py`: Earlier local-machine aircraft telemetry helpers kept for reference.

## Important Hosts And Paths

- RDK SSH during development: `ssh sunrise@192.168.50.175`, password known to the operator.
- RDK app path: `/home/sunrise/uav_tuya_agent`.
- Local mirror path: `/Users/mr.think/Documents/竞赛/物联网/uav_tuya_agent`.
- Aircraft board SSH: `ssh elf@192.168.2.2`, password known to the operator.
- Aircraft bridge path: `/home/elf/project/Light/WI-FI/OnboardBridge`.
- Local aircraft mirror: `/Users/mr.think/Documents/竞赛/Light/project/Light/WI-FI/OnboardBridge`.

Do not commit or print Tuya Access Secret, device secret, or other long-lived secrets. Use environment variables for cloud credentials.

## Common Commands

Run RDK rover stack manually:

```bash
cd /home/sunrise/uav_tuya_agent
./start_rover_stack.sh
```

Install normal development boot services on RDK:

```bash
cd /home/sunrise/uav_tuya_agent
sudo ./install_rdk_boot_services.sh
```

This enables:

```bash
uav-l610-primary.service
uav-rover-stack.service
```

Enable final demo Wi-Fi telemetry mode on RDK:

```bash
cd /home/sunrise/uav_tuya_agent
sudo ./install_mengchuang_boot_service.sh
```

Recover SSH after RDK switches to `mengchuang`:

```bash
sudo systemctl disable --now uav-mengchuang-link.service
nmcli con up "Mr.think的Mate 70 Pro+" ifname wlan0
```

Check RDK network state:

```bash
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status
ip -br addr
ip route
ip route get 139.196.6.123
tail -n 120 /tmp/l610-primary.log
```

Run the repository test suite:

```bash
python3 -m unittest discover -s tests -t . -v
```

Run the imported RDK baseline tests locally or on RDK:

```bash
cd rdk_agent
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

Run Tuya cloud ground station locally:

```bash
cd tuya_cloud_ground_station
HOST=127.0.0.1 PORT=5178 \
TUYA_ACCESS_ID="..." \
TUYA_ACCESS_SECRET="..." \
TUYA_DEVICE_ID="262c1deefdc2650445bwpd" \
node server.js
```

Open:

```text
http://127.0.0.1:5178/
```

## Development Rules

- Keep RDK default development mode SSH-friendly. Do not make `mengchuang` auto-connect in the main boot service.
- Treat `uav-mengchuang-link.service` as a final demo switch only.
- L610 should be the cloud/default route when its ECM interface is available. Wi-Fi must use `ipv4.never-default yes` so it does not steal cloud traffic.
- Avoid PPP on L610 unless the operator explicitly accepts SSH risk.
- Aircraft commands must be MAVLink2 frames. Current implementation hand-builds `0xFD` MAVLink2 packets in `aircraft_commands.py`.
- Do not send aircraft commands if `aircraft_state.json` is stale. The current stale threshold protects against sending to old `mengchuang` addresses.
- Keep manual rover control conservative: stop is always safe, throttle/steering are bounded, and command timeouts should neutralize control.
- Prefer small, testable modules. Add tests under `uav_tuya_agent/tests/` for protocol or safety changes.
- Do not rely on local computer Wi-Fi for final aircraft telemetry. Final path is aircraft telemetry module <-> RDK Wi-Fi <-> RDK agent <-> Tuya/L610 <-> cloud ground station.

## Known Current Risk

At the last recorded check, RDK's L610 ECM interface `enxf04bb3b9ebe5` appeared as `DOWN/unavailable`, so Tuya cloud route was unreachable even though service ordering was fixed. The next debugging step is to bring L610 ECM back up without changing Wi-Fi away from the SSH hotspot.
