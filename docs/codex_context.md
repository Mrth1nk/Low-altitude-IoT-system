# Codex Context

This file preserves the important operational context from the Codex debugging session for the RDK X5 + L610 + TuyaLink + ArduPilot Rover competition project.

## System Goal

The competition system is a low-altitude delivery / rover demo:

- RDK X5 sits on the rover/car and is the main rover-side computer.
- Fibocom L610 provides cellular connectivity to TuyaLink.
- TuyaLink carries rover telemetry and control commands.
- ArduPilot Rover firmware controls the car through MAVLink.
- The aircraft board sends MAVLink telemetry through a serial Wi-Fi telemetry module named `mengchuang`.
- RDK should receive aircraft telemetry from `mengchuang`, include compact aircraft status in `rover_state`, and send MAVLink2 commands back to the aircraft when requested.

## Key Addresses And Paths

- RDK SSH during development: `sunrise@192.168.50.175`.
- RDK project path: `/home/sunrise/uav_tuya_agent`.
- Local RDK mirror: `/Users/mr.think/Documents/竞赛/物联网/uav_tuya_agent`.
- Aircraft board SSH: `elf@192.168.2.2`.
- Aircraft project path: `/home/elf/project/Light/WI-FI/OnboardBridge`.
- Local aircraft mirror: `/Users/mr.think/Documents/竞赛/Light/project/Light/WI-FI/OnboardBridge`.
- Local Tuya cloud ground station: `/Users/mr.think/Documents/竞赛/物联网/tuya_cloud_ground_station`.
- Local Tuya cloud ground station URL: `http://127.0.0.1:5178/`.
- Tuya device ID used by cloud station: `262c1deefdc2650445bwpd`.
- Tuya product/project pages were operated through the Tuya developer platform. Do not store secrets in this file.

## Network Design

Development mode:

- RDK `wlan0` stays on phone hotspot `Mr.think的Mate 70 Pro+`.
- SSH remains available at `192.168.50.175`.
- `uav-mengchuang-link.service` should be disabled.

Cloud route:

- L610 should expose ECM USB network interface, previously seen as `enxf04bb3b9ebe5`.
- L610 should be the default route for Tuya/cloud traffic when available.
- Wi-Fi profiles should use `ipv4.never-default yes` to avoid stealing cloud traffic.

Final demo mode:

- Run `sudo ./install_mengchuang_boot_service.sh` on RDK.
- RDK `wlan0` connects to `mengchuang`.
- RDK usually receives `192.168.4.2/24`.
- Wi-Fi telemetry module is expected at `192.168.4.1`.
- SSH over phone hotspot will likely disconnect after switching to `mengchuang`.

Recover SSH:

```bash
sudo systemctl disable --now uav-mengchuang-link.service
nmcli con up "Mr.think的Mate 70 Pro+" ifname wlan0
```

## RDK Services

Normal development boot services:

```bash
uav-l610-primary.service
uav-rover-stack.service
```

Install/reinstall:

```bash
cd /home/sunrise/uav_tuya_agent
sudo ./install_rdk_boot_services.sh
```

Final demo service:

```bash
uav-mengchuang-link.service
```

Enable final demo service:

```bash
cd /home/sunrise/uav_tuya_agent
sudo ./install_mengchuang_boot_service.sh
```

## Tuya Cloud Station

The local computer cloud station is in `tuya_cloud_ground_station`.

Run it with environment variables:

```bash
cd /Users/mr.think/Documents/竞赛/物联网/tuya_cloud_ground_station
HOST=127.0.0.1 PORT=5178 \
TUYA_ACCESS_ID="..." \
TUYA_ACCESS_SECRET="..." \
TUYA_DEVICE_ID="262c1deefdc2650445bwpd" \
node server.js
```

It reads Tuya OpenAPI state endpoints and sends product-defined command properties only:

- `command`
- `target_lat`
- `target_lng`
- `target_speed`
- `steering`
- `throttle`

Earlier token invalid issues were handled by token refresh logic.

## Rover Commands

Rover commands are represented by `RoverCommand` in `rover_state.py` and applied by `tuya_rover_agent.py`.

Important safety behavior:

- `stop` should always be safe.
- Manual drive commands are bounded.
- Manual drive has a timeout and should neutralize after no refresh.
- Waypoints require valid coordinates.

## Aircraft Telemetry And Commands

RDK receives aircraft UDP telemetry through `aircraft_gateway.py` on UDP ports `14560` and `14550`.

Aircraft state path on RDK:

```text
/home/sunrise/uav_tuya_agent/aircraft_state.json
```

Example observed last remote:

```text
192.168.4.1:14555 -> UDP 14560
```

Aircraft commands:

- `aircraft_guided`
- `aircraft_loiter`
- `aircraft_rtl`
- `aircraft_land`
- `aircraft_arm`
- `aircraft_disarm`

They are handled in `aircraft_commands.py`.

Important packet decision:

- Commands must be MAVLink2.
- Current code hand-builds MAVLink2 frames with magic byte `0xFD`.
- Mode commands send both `COMMAND_LONG MAV_CMD_DO_SET_MODE` and `SET_MODE`.
- Arm/disarm sends `COMMAND_LONG MAV_CMD_COMPONENT_ARM_DISARM`.
- Packets are repeated to the most recent remote port plus common MAVLink ports `14555`, `14550`, and `14560`.
- If aircraft telemetry state is stale, command send is rejected to avoid sending to an old target.

Verification performed:

- Local RDK command packet tests passed via direct assertions.
- RDK-deployed builder printed `RDK aircraft MAVLink2 builder OK mode_lens=45,18 arm_len=45`.
- Aircraft bridge monitor parsed RDK packets as `COMMAND_LONG command=176` and `SET_MODE custom_mode=4`.

## Aircraft Board Fix

The aircraft bridge had been blocked by stale optical link state:

- `fc_to_wifi_bytes` was zero or blocked.
- `/tmp/optical_link_status.json` stayed stale with `link_blocked:true`.

Fix applied in aircraft project:

- `optical_link_state.py` now uses PID-specific temp files before replacing the status file.
- This avoided temp-file collisions / stale replace behavior.

After restarting the aircraft precision/bridge service, optical state became fresh and bridge forwarding resumed.

## Known Current Issue

At the last recorded RDK network check:

```text
enxf04bb3b9ebe5:ethernet:unavailable
RTNETLINK answers: Network is unreachable
```

This means L610 ECM was not up, so Tuya/cloud route could not work regardless of the Wi-Fi route settings.

Next debug should keep RDK on phone hotspot and inspect only L610:

```bash
lsusb
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status
ip -br link
dmesg | tail -n 120 | grep -Ei 'usb|cdc|ether|rndis|l610|fibocom'
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
cd /home/sunrise/uav_tuya_agent
. .venv/bin/activate
python tuya_rover_agent.py check-l610 --at-port /dev/ttyUSB0
```

Do not enable `uav-mengchuang-link.service` while debugging L610 unless the operator is ready to lose phone-hotspot SSH.
