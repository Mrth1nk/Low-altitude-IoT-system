# TASKS.md

## 2026-07-26 Vision Recovery

### Completed

- Reproduced the failure against the successful aircraft reference scripts.
- Confirmed camera detection and UART9 MAVLink TX/RX independently.
- Replaced altitude-dependent GUIDED control with the successful pixel-based
  forward/right correction semantics.
- Restored angle-only BODY_FRD precision-landing output for unknown altitude.
- Fixed camera reopen configuration and added live optical diagnostics.
- Deployed and backed up the ELF vision package without rebooting the board.
- Verified disarmed `GUIDED` and `LAND` transmissions, then restored
  `STABILIZE`.

### Remaining

1. Perform the supervised propeller-off visual alignment check with the real
   aircraft geometry.
2. Perform the outdoor powered flight and final landing rehearsal.

## 2026-07-24 Refactor Status

### Completed

- Imported the proven RDK, Tuya cloud ground station, ELF bridge, and infrared
  vision baseline without committing credentials or runtime state.
- Added the authenticated LIOT v1 protocol with bounded retry, replay
  protection, restart-safe counters, command IDs, mission checksums, and
  split/concatenated stream recovery.
- Preserved the working L610/Tuya route while separating Rover commands from
  aircraft commands.
- Replaced Rover point-by-point goto handling with an asynchronous, verified
  MAVLink mission transaction:
  - request-driven `MISSION_ITEM_INT` upload;
  - accepted ACK validation;
  - mission download and field-by-field readback;
  - indoor upload verification without fake GPS or Home;
  - real GPS/Home/EKF execution gate before AUTO;
  - bounded queue, stale-command rejection, and confirmed failure cleanup.
- Added the ELF durable command inbox and optical gate:
  - `/dev/ttyUSB0` serial-stream framing for the transparent Wi-Fi telemetry
    module;
  - atomic replace plus file and directory fsync before ACK;
  - restart recovery, duplicate suppression, missing-item resume, and bounded
    storage;
  - one active command plus a bounded FIFO;
  - `LINK_BLOCKED`-only output and command rejection while the beacon is lost;
  - one-hertz aircraft state while the optical link is locked.
- Added single-owner ELF MAVLink handling, verified aircraft mission upload
  and readback, isolated GUIDED tracking, and angle-only precision landing.
- Deployed the replacement ELF services and verified the real camera, flight
  controller, telemetry serial device, display manager, and atomic snapshots.
- Deployed the replacement RDK service without changing the development Wi-Fi:
  - L610 remains the Tuya/default route;
  - the old Rover service is disabled transactionally;
  - the Tuya agent is the sole owner of aircraft UDP ports;
  - MQTT property reports return Tuya `code: 0`.
- Finished the Tuya-only dual-vehicle ground station with complete Rover and
  aircraft missions, separate aircraft AUTO, optical blocking, fixed message
  timestamps, mission timelines, alerts, and responsive layout.
- Added read-only ArduPilot parameter backup/audit tools. No flight-controller
  parameter change is currently required; `PLND_EST_TYPE=0` is preserved.
- Added deterministic fault-injection integration tests and a read-only demo
  preflight/runbook.
- Verified the current repository with 210 Python tests (`1` OpenCV-dependent
  skip), 10 Node tests, syntax checks, browser layout checks, and
  `git diff --check`.

### In Progress

- Preparing the final RDK `woshinailong` boot profile and end-to-end optical
  link rehearsal without disrupting the current SSH development connection.

### Remaining

1. Activate the prepared `woshinailong` profile for the final demo and run both
   role-specific preflight checks.
2. Run the supervised propeller-off optical-lock and aircraft command rehearsal.
3. Run outdoor Rover GPS mission verification and staged flight tests.
4. Push the verified branch to `Mrth1nk/Low-altitude-IoT-system`.

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

- Final physical rehearsal of the prepared `woshinailong` link. Aircraft
  mission upload and flight-controller readback have already been verified on
  the real ELF/ArduPilot connection without entering AUTO.

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

- Outdoor navigation and powered flight validation remain supervised field
  activities; software checks do not replace the physical safety checklist.
