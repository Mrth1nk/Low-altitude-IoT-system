# TASKS.md

## Done

- Built RDK-side rover agent under `uav_tuya_agent/`.
- Split Tuya auth, L610 helpers, rover state, MAVLink rover control, Tuya MQTT agent, and local web ground station into separate modules.
- Added RDK local ground station on port `8080`.
- Added Tuya cloud ground station under `tuya_cloud_ground_station/` on local port `5178`.
- Configured Tuya cloud station to read device shadow/status APIs and refresh token on token invalid errors.
- Added command filtering so only product-defined command properties are sent to Tuya.
- Added compact `rover_state` reporting to fit Tuya string limits.
- Added aircraft message summary into rover cloud state.
- Added RDK aircraft UDP gateway for `mengchuang` MAVLink telemetry on UDP `14560` / `14550`.
- Fixed aircraft bridge optical link status file update on the ELF board by using PID-specific temp files.
- Restored aircraft bridge forwarding after stale optical-blocked state.
- Changed aircraft command downlink to hand-built MAVLink2 packets instead of the earlier potentially empty `pymavlink` packet path.
- Aircraft mode commands now send both MAVLink2 `COMMAND_LONG MAV_CMD_DO_SET_MODE` and MAVLink2 `SET_MODE`.
- Aircraft arm/disarm now sends MAVLink2 `COMMAND_LONG MAV_CMD_COMPONENT_ARM_DISARM`.
- Added `uav_tuya_agent/tests/test_aircraft_commands.py` to validate MAVLink2 packet shape and send destinations.
- Added normal RDK boot service installer `install_rdk_boot_services.sh`.
- Added separate final-demo `mengchuang` boot service installer `install_mengchuang_boot_service.sh`.
- Changed normal boot flow so `uav-mengchuang-link.service` is disabled by default and does not break SSH unexpectedly.
- Confirmed RDK normal services can run as:
  - `uav-l610-primary.service`
  - `uav-rover-stack.service`
- Updated and deployed the RDK local ground station to `192.168.43.175`:
  - Left side shows compact rover map, status, controls, and log.
  - Right side shows aircraft Wi-Fi telemetry messages without opening a modal.
  - Aircraft message rows use fixed receive timestamps and repeated messages are compacted.
  - Manual rover buttons and keyboard control now use full-scale throttle for forward/reverse.
- Corrected the competition ground station path:
  - The operator-facing ground station is the local Tuya cloud station at `http://127.0.0.1:5178/`.
  - It reads rover and aircraft state from Tuya Cloud `rover_state`, not from the RDK local IP.
  - It now uses the same left rover / right aircraft layout and full-scale rover manual throttle.
  - Compact cloud aircraft messages keep stable timestamps instead of changing on every refresh.
- Fixed aircraft mission upload reliability on the RDK:
  - `tuya_rover_agent.py run` now starts the aircraft UDP gateway itself, so Tuya cloud state no longer depends on the RDK local ground-station web server being open.
  - Aircraft mission upload now reads `MISSION_REQUEST(_INT)` and `MISSION_ACK` from its own UDP command socket as well as `aircraft_state.json`.
  - This avoids losing mission handshake replies when the sender socket and gateway share UDP `14560`.
  - RDK `.venv` test run passed: `python -m unittest discover -s tests -v` with 14 tests.
- Installed `sshpass` locally for faster RDK and ELF SSH operations during debugging.
- Fixed aircraft message display truncation through Tuya:
  - The raw RDK aircraft state was not truncated.
  - `rover_state` compacting now keeps the latest aircraft message text readable while staying under the Tuya string limit.
  - Verified RDK Tuya property reports returned `code:0` after the fix.

## In Progress

- Stabilizing the final demo mode where RDK `wlan0` switches from the phone hotspot to `mengchuang` while L610 remains the cloud/default route.
- Validating final demo sequence where RDK Wi-Fi connects to `mengchuang`, receives aircraft telemetry, and sends MAVLink2 aircraft commands.
- Aircraft mission upload still needs flight-controller-side confirmation: current logs show RDK sends mission upload attempts, but the FC does not return `MISSION_REQUEST_INT` through the bridge during the tested window.

## Next Steps

1. With RDK on phone hotspot for SSH, confirm current development routing:

   ```bash
   lsusb
   nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status
   ip -br link
   dmesg | tail -n 120 | grep -Ei 'usb|cdc|ether|rndis|l610|fibocom'
   ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
   ```

2. Confirm L610 ECM stays the default cloud route without switching Wi-Fi:

   ```bash
   cd /home/sunrise/uav_tuya_agent
   sudo ./configure_l610_primary.sh
   ip route get 139.196.6.123
   ```

3. If `enxf04bb3b9ebe5` remains unavailable, check L610 USB mode and AT port:

   ```bash
   cd /home/sunrise/uav_tuya_agent
   . .venv/bin/activate
   python tuya_rover_agent.py check-l610 --at-port /dev/ttyUSB0
   ```

4. Once L610 route is confirmed, verify cloud control from the Tuya cloud ground station.

5. Only when ready for final aircraft telemetry demo, enable `mengchuang`:

   ```bash
   cd /home/sunrise/uav_tuya_agent
   sudo ./install_mengchuang_boot_service.sh
   ```

6. After switching to `mengchuang`, verify:

   - RDK gets `wlan0` address around `192.168.4.2`.
   - Aircraft state updates in `/home/sunrise/uav_tuya_agent/aircraft_state.json`.
   - `rover_agent.log` contains `starting aircraft UDP gateway ports=[14560, 14550]`.
   - Ground station aircraft panel shows fresh messages.
   - Aircraft command buttons create RDK log lines with `aircraft ... mission uploaded and AUTO sent` or clear mission-upload errors.
   - ELF bridge log shows `[WiFi->FC] MISSION_COUNT`, `[FC->WiFi] MISSION_REQUEST`, and `Flight plan received`.

7. If SSH must be recovered:

   ```bash
   sudo systemctl disable --now uav-mengchuang-link.service
   nmcli con up "Mr.think的Mate 70 Pro+" ifname wlan0
   ```

## Deferred

- Task 5 / ELF authenticated LIOT integration: the RDK transport now requires
  HMAC-authenticated datagrams, but cross-restart session challenge and ELF codec
  compatibility are not complete. Before deploying this revision to the production
  RDK or live aircraft link, update the ELF bridge to use
  `shared_protocol.auth.AuthenticatedDatagramCodec` with the same untracked
  `AIRCRAFT_LINK_PSK` and complete the restart handshake design.
- Task 11: ground-station full-mission editor and OpenAPI mission payload/filtering.
  The runtime accepts normalized complete mission payloads, but this task deliberately
  does not change either ground-station implementation or its per-point UI.
- Local macOS system Python 3.9 does not include `paho-mqtt`; pure root tests stay
  isolated from that optional import. Run the Tuya authentication baseline in the
  RDK virtual environment, where the dependency is installed.
- Improve RDK README IP examples from old `192.168.43.175` to current development hotspot IP pattern.
- Add a one-command health report script for RDK network, L610, Tuya MQTT, rover FC, and aircraft gateway.
- Add browser-side indicator that explicitly shows whether aircraft command downlink is blocked because `aircraft_state.json` is stale.
- Add safer final-demo runbook with numbered operator steps.
