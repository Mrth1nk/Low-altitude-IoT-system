# CHANGELOG.md

## 2026-08-19

- Preserved the successful single-aircraft system with recovery tag
  `national-finals-single-aircraft-baseline-20260819`.
- Added strict typed UDP messages for a second RDK-X5 aircraft node, including
  node identity, sequence, command ID, status, ACK/NACK and mission digest.
- Added the slave RDK runtime with durable admission, deduplication, ordered
  mission execution, flight-controller verification and a reserved optical
  gate that defaults to unblocked.
- Added atomic slave installation, fixed `by-id` flight-controller preflight,
  serial ownership checks, health checks and rollback support.

## 2026-08-06

- Added a configurable precision-landing final-descent cutoff at 0.25 m.
  Once entered in `LAND`/`QLAND`, the vision service stops sending new
  `LANDING_TARGET` corrections until the aircraft leaves the landing mode.
- Preserved GUIDED infrared tracking and ignored zero or unknown altitude so
  indoor startup cannot accidentally enter final descent.
- Read the flight controller without changing parameters: `PLND_ALT_MIN=0.75`
  and `PLND_STRICT=1` keep target loss at 0.25 m in the vertical-descent zone.
- Created Git restore tag `vision-working-before-final-descent-20260806` and
  deployed with an ELF backup at
  `/home/elf/vision-backups/20260806-105025`.
- Fixed Rover mission execution readiness becoming stale after an indoor
  upload. `AUTO` now refreshes Home, GPS, EKF, and telemetry freshness at the
  moment the command is received.
- Changed Rover `AUTO` to use the existing heartbeat-confirmed mode switch and
  report the last observed flight-controller mode when confirmation fails.
- Added explicit `mission re-upload required before AUTO` reporting after an
  agent restart instead of silently remaining in `HOLD`.
- Preserved real flight-controller `0,0` coordinates for indoor diagnostics by
  reporting a separate `position_observed` flag. The map and mission planner
  still reject `0,0` as a navigation coordinate.
- Kept both observed Rover and aircraft positions plus Rover AUTO failure
  details in the 480-byte Tuya compact state.
- Deployed only the four affected RDK agent modules to
  `/opt/low-altitude-iot/current/rdk_agent`, restarted only
  `low-altitude-rdk.service`, and confirmed zero automatic restarts.
- Verified the real Tuya command path indoors: an `AUTO` request reached the
  RDK and returned GPS fix `1`, zero satellites, and the explicit mission
  re-upload requirement while the Rover remained safely in `HOLD`.
- Restarted the local Tuya ground station and confirmed both vehicles display
  their real observed `0,0` coordinates instead of cached outdoor positions.
- Fixed a 480-byte compact-state regression where an accepted aircraft command
  could evict the nested aircraft heartbeat. Live aircraft mode, armed state,
  position, altitude, heading, and one heartbeat now take priority over
  expendable Rover control fields.
- Switched the RDK to `woshinailong` through Tuya and verified that an accepted
  `aircraft_guided` command no longer interrupts subsequent aircraft
  heartbeats or causes a false optical-blocked display.

## 2026-08-05

- Corrected GUIDED infrared tracking for the forward-mounted camera:
  - image-up now commands positive aircraft-forward velocity;
  - image-down commands reverse velocity;
  - image-left/right retain matching body-right signs.
- Added altitude-aware compensation for the camera mounted 4 cm forward and
  1 cm right of the aircraft origin. Unknown or zero altitude safely falls
  back to the geometric image center.
- Deployed only `vision/guided_tracker.py` and `vision/mode_controller.py` to
  ELF, restarted only `low-altitude-vision.service`, and created a reversible
  backup at `/home/elf/vision-backups/20260805-184055`.
- Verified the deployed service remained active with zero restarts, local and
  remote hashes matched, and live GUIDED transmit counters continued rising.
- Fixed intermittent local ground-station disconnects caused by excessive
  Tuya state API reads.
- Replaced three unconditional state calls per refresh with a primary-first,
  fallback-on-error reader cached for two seconds.
- Coalesced concurrent state reads and retained the last successful state
  during transient Tuya `server busy` responses while preserving normal stale
  telemetry detection.
- Removed the permanently unauthorized legacy v1 status endpoint from the
  state-read path.
- Left Tuya command issue requests uncached and unchanged.

## 2026-08-04

- Fixed intermittent Rover and aircraft marker disappearance when Tuya returned
  partial mission frames or temporary no-fix coordinates.
- Preserved both vehicle coordinates in compact Rover mission-state reports
  while remaining below the Tuya 480-byte payload limit.
- Changed Rover mode control to wait for flight-controller `HEARTBEAT`
  confirmation and report the observed mode on failure.
- Blocked Rover `AUTO` unless the current session has a verified,
  execution-ready mission.
- Deployed the updated RDK agent without rebooting the board and restarted the
  local ground station on `127.0.0.1:5178`.
- Verified a real `HOLD` command through the full Tuya path and confirmed that
  indoor non-ready `AUTO` is rejected without changing the flight mode.

## 2026-07-26

- Restored aircraft GUIDED infrared tracking to the proven reference control
  semantics:
  - pixel-normalized image error;
  - 25 px deadzone;
  - forward sign matching the successful `precision_land_v4` behavior;
  - 0.35 m/s bounded output at 10 Hz;
  - no dependence on a valid altitude source.
- Restored indoor-safe angle-only precision landing behavior:
  - emits BODY_FRD `LANDING_TARGET` with `position_valid=0`;
  - continues to work when altitude is unavailable;
  - applies the configured 4 cm forward and 1 cm right camera offsets when
    altitude is valid.
- Configured every camera open/reopen to 640x480 with a one-frame buffer.
- Added optical diagnostics for frame size, target coordinates, pixel error,
  and actual GUIDED/LAND transmission counters.
- Deployed only the vision package to ELF with a reversible backup under
  `/home/elf/vision-backups/20260726-034508`; no board reboot was performed.
- Verified disarmed mode transitions and live transmissions:
  `GUIDED` emitted 21 velocity messages, `LAND` emitted 21 landing targets,
  then the aircraft was restored to `STABILIZE`.
- Full repository verification passed: 242 Python tests, 1 expected OpenCV
  skip, compile checks, and `git diff --check`.

## 2026-07-24

- Started the reliable dual-vehicle refactor on
  `refactor/reliable-system`.
- Added the LIOT v1 framed protocol and typed Rover/aircraft command routing.
- Added HMAC authentication, replay protection, restart-safe session state, and
  a challenge exchange for the RDK-to-ELF link.
- Replaced Rover goto queuing with asynchronous MAVLink mission upload,
  accepted-ACK validation, download/readback verification, and real navigation
  readiness gates.
- Added an ELF durable inbox for complete aircraft mission staging before any
  flight-controller handshake begins.
- Added serial-stream handling for the real `woshinailong` topology: RDK UDP to
  the Wi-Fi telemetry module, then transparent bytes on ELF `/dev/ttyUSB0`.
- Added strict optical-link behavior: cloud aircraft commands are rejected and
  only timestamped `LINK_BLOCKED` crosses the link while the infrared target is
  lost.
- Added a single-reader ELF MAVLink session and transactional aircraft mission
  upload with Home handling, request/ACK negotiation, strict readback, indoor
  verification, cleanup, and an explicit execution-readiness gate.
- Rebuilt GUIDED infrared tracking and precision landing as isolated vision
  services using the real forward-facing camera and configured camera offset.
- Installed and verified the replacement ELF services while preserving the
  desktop and root-directory permissions.
- Added reversible RDK installers, root-only runtime secrets, SSH-friendly
  development networking, and an optional final-demo Wi-Fi service.
- Migrated the production RDK agent while keeping the L610 ECM interface as the
  Tuya/default route; Tuya property reports were confirmed with `code: 0`.
- Removed duplicate ownership of aircraft UDP ports between the RDK cloud agent
  and local debug server.
- Added read-only ArduPilot parameter backup/audit tooling and documented that
  no parameter write is currently required.
- Added protocol fault injection, durable restart tests, fake flight-controller
  mission verification, and a read-only competition preflight/runbook.
- Rebuilt the operator ground station around Tuya Cloud OpenAPI with dual
  vehicle missions, route queues, aircraft altitude, separate AUTO, optical
  command blocking, alerts, fixed message timestamps, and responsive layout.
- Fixed CSS hidden-state handling after browser validation found a false
  `OPTICAL LINK BLOCKED` banner.

## 2026-07-23

- Added project maintenance documentation:
  - `AGENTS.md`
  - `TASKS.md`
  - `CHANGELOG.md`
  - `docs/codex_context.md`
- Updated the RDK local ground station:
  - Changed the main dashboard to a left rover / right aircraft split layout.
  - Made the aircraft message panel always visible instead of requiring a modal.
  - Removed the live aircraft delay timer from the visible status cards and shows fixed received-message timestamps instead.
  - Compacts repeated aircraft messages with a count suffix.
  - Simplified rover status cards for faster scanning.
  - Increased manual drive button and keyboard throttle output to full-scale forward/reverse.
- Updated the local Tuya cloud ground station in `tuya_cloud_ground_station/`:
  - Made the browser ground station layout left rover / right aircraft.
  - Kept the data source on Tuya Cloud `rover_state` instead of the RDK local API.
  - Removed the live aircraft delay timer from the visible UI.
  - Uses stable received/change timestamps for compact cloud aircraft messages.
  - Increased rover manual drive button and keyboard throttle output to full-scale forward/reverse.
- Added Tuya cloud ground-station operator features:
  - Arrival popup with target, rover position, and distance error.
  - Mission status banner for idle, moving, arrived, GPS wait, and failure states.
  - Low battery, weak LTE, aircraft-link, and geofence toast alerts.
  - Cloud command receipt toasts.
  - Rover GPS track line on the map.
  - Waypoint queue with send-next workflow.
  - Electronic geofence radius control.
- Fixed aircraft mission/waypoint upload path through RDK:
  - `tuya_rover_agent.py run` now starts the aircraft UDP gateway on `14560` / `14550`.
  - This removes the hidden dependency on the RDK local `ground_station_server.py`, which was failing when port `8081` was already used by the stereo depth web service.
  - Mission upload now drains `MISSION_REQUEST(_INT)` and `MISSION_ACK` from the command socket itself while still also checking `aircraft_state.json`.
  - Added tests for command-socket mission replies and rover-agent aircraft gateway startup.
  - Deployed the fix to `/home/sunrise/uav_tuya_agent` on the RDK and restarted `uav-rover-stack.service`.
  - Verified on the RDK `.venv` with `python -m unittest discover -s tests -v` passing 14 tests.
- Installed `sshpass` locally to simplify repeat SSH/SCP operations during hardware debugging.
- Fixed Tuya cloud aircraft message truncation:
  - Confirmed RDK `aircraft_state.json` contained full messages such as `心跳 GUIDED armed=NO sys=1/1`.
  - Found truncation in `rover_state` compacting, where the payload could fall back to short `aircraft_msg` text.
  - Kept `rover_state` within the Tuya-accepted size while prioritizing the latest aircraft message text.
  - Verified RDK property reports returned `code:0` again after an oversized 1600-byte attempt returned `code:2006`.
  - Added a regression test ensuring compacted cloud state preserves full aircraft message text.

## 2026-07-22

- Built the RDK rover/TuyaLink application in `uav_tuya_agent/`.
- Added modular Python files:
  - `tuya_auth.py`
  - `l610.py`
  - `rover_state.py`
  - `mavlink_rover.py`
  - `tuya_rover_agent.py`
  - `ground_station_server.py`
  - `aircraft_gateway.py`
  - `aircraft_commands.py`
- Added RDK local web ground station:
  - `web/index.html`
  - `web/app.js`
  - `web/styles.css`
- Added tests for auth, L610 parsing, rover state, MAVLink rover helpers, aircraft gateway, and aircraft MAVLink2 commands.
- Added L610 route script `configure_l610_primary.sh`.
  - Loads `usbnet` and `cdc_ether`.
  - Sets L610 NetworkManager profile as default route when available.
  - Sets phone hotspot and `mengchuang` Wi-Fi profiles as `never-default`.
  - Adds host route to `192.168.4.1` through `wlan0` for the Wi-Fi telemetry module.
- Added stack starter `start_rover_stack.sh`.
  - Starts `tuya_rover_agent.py run`.
  - Starts `ground_station_server.py --host 0.0.0.0 --port 8080`.
  - Supports `SKIP_L610_CONFIG=1` for systemd service mode.
- Added boot service installer `install_rdk_boot_services.sh`.
  - Installs `uav-l610-primary.service`.
  - Installs `uav-rover-stack.service`.
  - Runs rover stack as user `sunrise`.
  - Disables `uav-mengchuang-link.service` by default to preserve SSH during development.
- Added final-demo `mengchuang` installer `install_mengchuang_boot_service.sh`.
  - Installs and enables `uav-mengchuang-link.service`.
  - Connects RDK `wlan0` to `mengchuang`.
  - Keeps `mengchuang` as `ipv4.never-default yes`.
- Added Tuya cloud ground station in `tuya_cloud_ground_station/`.
  - Uses Tuya OpenAPI HMAC signing.
  - Reads shadow/state/status endpoints.
  - Refreshes token automatically after token invalid errors.
  - Sends only allowed command properties.
- Updated cloud station UI with rover controls, aircraft panel, and command logs.
- Added compact `rover_state` cloud payload handling so Tuya string fields stay within practical limits.
- Routed cloud aircraft commands through RDK:
  - `aircraft_guided`
  - `aircraft_loiter`
  - `aircraft_rtl`
  - `aircraft_land`
  - `aircraft_arm`
  - `aircraft_disarm`
- Fixed aircraft downlink command packet generation:
  - Removed dependency on the earlier `pymavlink` pack path that could produce empty packets.
  - Hand-builds MAVLink2 `0xFD` frames.
  - Mode commands send both `COMMAND_LONG MAV_CMD_DO_SET_MODE` and `SET_MODE`.
  - Arm/disarm sends `COMMAND_LONG MAV_CMD_COMPONENT_ARM_DISARM`.
  - Sends repeated packets to recent remote port plus `14555`, `14550`, and `14560`.
- Verified locally that RDK MAVLink2 command packets can be parsed by the aircraft bridge monitor as `COMMAND_LONG` and `SET_MODE`.
- On the ELF aircraft bridge, fixed stale optical link status writes by using PID-specific temp files before replacing the status file.
- Confirmed aircraft bridge telemetry receive path recovered after optical state fix.
- Known unresolved issue at end of day: RDK L610 ECM interface `enxf04bb3b9ebe5` was observed as `DOWN/unavailable`, leaving Tuya route unreachable until L610 is brought back up.
