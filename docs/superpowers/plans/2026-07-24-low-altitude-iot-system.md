# Low-altitude IoT System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a tested, deployable Tuya-controlled Rover and aircraft system with reliable aircraft mission staging, verified MAVLink mission uploads, infrared GUIDED tracking, precision landing, and strict optical-link gating.

**Architecture:** Preserve the proven L610/Tuya implementation and place stable interfaces around it. Introduce a framed, idempotent RDK-to-ELF protocol and an ELF-local mission transaction worker so wireless delivery completes before MAVLink mission upload begins. Keep visual control on its dedicated flight-controller UART while the aircraft agent owns USB mission transactions.

**Tech Stack:** Python 3.10+, pymavlink, pyserial, OpenCV, NumPy, Paho MQTT, Node.js, browser JavaScript, systemd, NetworkManager, TuyaLink/OpenAPI.

---

## File Map

- `shared_protocol/frame.py`: binary frame envelope, checksum, stream decoder.
- `shared_protocol/messages.py`: validated command, mission, acknowledgement, and status payloads.
- `shared_protocol/transport.py`: retry, acknowledgement, deduplication, and resume logic.
- `rdk_agent/`: preserved Tuya/L610 code plus command router, Rover mission worker, and aircraft link client.
- `aircraft_agent/`: reliable link server, durable inbox, FC transaction worker, telemetry snapshot, and service entrypoint.
- `vision/`: detector, geometry, mode controller, optical state, and precision landing.
- `ground_station/`: preserved Tuya OpenAPI server and rebuilt state/mission UI.
- `ops/`: deployment, systemd, network, serial identity, preflight, and parameter recovery tools.
- `tests/`: shared protocol, mission state machine, optical gate, replay, and integration tests.

### Task 1: Import Stable Baseline And Add Repository Documentation

**Files:**
- Create: `AGENTS.md`
- Create: `TASKS.md`
- Create: `CHANGELOG.md`
- Create: `docs/codex_context.md`
- Create: `rdk_agent/`
- Create: `ground_station/`
- Create: `aircraft_agent/legacy/`
- Create: `vision/legacy/`

- [ ] Copy the current stable RDK and Tuya ground-station source into the new repository without secrets, logs, runtime state, virtual environments, or generated files.
- [ ] Copy the current ELF bridge and visual source into clearly marked baseline locations.
- [ ] Replace absolute credential values with environment/config examples containing non-secret placeholders.
- [ ] Run `rg -n '(Access Secret|device_secret|TR6a|1234567)' .` and verify no long-lived secret is present.
- [ ] Run existing Python and Node syntax tests to establish the baseline.
- [ ] Commit with `chore: import proven competition baseline`.

### Task 2: Implement Shared Reliable Frame Protocol

**Files:**
- Create: `shared_protocol/__init__.py`
- Create: `shared_protocol/frame.py`
- Create: `shared_protocol/messages.py`
- Create: `shared_protocol/transport.py`
- Create: `tests/shared_protocol/test_frame.py`
- Create: `tests/shared_protocol/test_transport.py`

- [ ] Write failing tests for split frames, concatenated frames, bad CRC, duplicate sequence numbers, out-of-order mission items, and reconnect resume.
- [ ] Define a length-prefixed frame with magic `LIOT`, version `1`, message type, flags, sequence, command UUID, payload length, JSON payload, and CRC32.
- [ ] Implement a streaming decoder that retains partial bytes and resynchronizes after malformed input.
- [ ] Implement typed messages for `COMMAND`, `MISSION_BEGIN`, `MISSION_ITEM`, `MISSION_COMMIT`, `ACK`, `NACK`, `STATUS`, and `LINK_BLOCKED`.
- [ ] Implement a bounded retry sender and a receiver deduplication window.
- [ ] Run `python3 -m unittest discover -s tests/shared_protocol -v`; expect all tests to pass.
- [ ] Commit with `feat: add reliable RDK aircraft protocol`.

### Task 3: Refactor RDK Command Routing Without Replacing Tuya/L610

**Files:**
- Create: `rdk_agent/command_router.py`
- Create: `rdk_agent/aircraft_link.py`
- Modify: `rdk_agent/tuya_rover_agent.py`
- Modify: `rdk_agent/rover_state.py`
- Create: `tests/rdk_agent/test_command_router.py`
- Create: `tests/rdk_agent/test_aircraft_link.py`

- [ ] Write failing tests showing Rover commands route only to the Rover executor and aircraft commands route only to the aircraft link.
- [ ] Preserve the current Tuya credential generation, MQTT lifecycle, L610 routing, property decoding, and one-second cloud reporting.
- [ ] Normalize each cloud command into a typed command with `command_id`, source timestamp, target vehicle, and payload.
- [ ] Reject stale commands and reject all aircraft commands while optical state is blocked.
- [ ] Stage complete aircraft missions through `MISSION_BEGIN`, indexed items, and `MISSION_COMMIT`; retry only missing/unacknowledged frames.
- [ ] Publish transaction stages in the compact Tuya state without exceeding the product string limit.
- [ ] Run all RDK tests in its virtual environment and verify no existing Tuya/L610 regression.
- [ ] Commit with `refactor: route rover and aircraft commands explicitly`.

### Task 4: Replace Rover Goto Queue With Verified MAVLink Missions

**Files:**
- Create: `rdk_agent/rover_mission.py`
- Modify: `rdk_agent/mavlink_rover.py`
- Create: `tests/rdk_agent/test_rover_mission.py`
- Create: `tests/fixtures/rover_mission_handshake.jsonl`

- [ ] Write a fake MAVLink transport test that requests mission items out of order and returns an accepted `MISSION_ACK`.
- [ ] Implement clear, count, request-driven item upload, acknowledgement, and mission download/readback.
- [ ] Compare coordinates with integer-degree precision tolerance and compare command/frame fields exactly.
- [ ] Allow indoor upload/readback but report `execution_ready=false` until GPS, Home, and EKF checks pass.
- [ ] Implement mission start in AUTO only after verification and implement endpoint completion state from `MISSION_CURRENT`/`MISSION_ITEM_REACHED`.
- [ ] Run Rover mission tests and the existing manual-control safety tests.
- [ ] Commit with `feat: verify rover mission uploads`.

### Task 5: Implement ELF Durable Inbox And Optical Gate

**Files:**
- Create: `aircraft_agent/inbox.py`
- Create: `aircraft_agent/link_server.py`
- Create: `aircraft_agent/optical_gate.py`
- Create: `aircraft_agent/state_store.py`
- Create: `tests/aircraft_agent/test_inbox.py`
- Create: `tests/aircraft_agent/test_optical_gate.py`

- [ ] Write tests for a restart during mission staging, duplicate frames, missing item requests, checksum mismatch, and blocked-link rejection.
- [ ] Persist staged commands using atomic file replacement and fsync before returning `MISSION_STAGED`.
- [ ] Keep one active aircraft command and a bounded FIFO queue; use command IDs for idempotency.
- [ ] When blocked, reject every inbound cloud aircraft command and emit only timestamped `LINK_BLOCKED`.
- [ ] When locked, permit commands and publish one aircraft snapshot per second.
- [ ] Run the aircraft-agent unit tests.
- [ ] Commit with `feat: add durable ELF command inbox`.

### Task 6: Implement Aircraft MAVLink Transaction Worker

**Files:**
- Create: `aircraft_agent/mavlink_session.py`
- Create: `aircraft_agent/mission_worker.py`
- Create: `aircraft_agent/command_worker.py`
- Create: `aircraft_agent/telemetry.py`
- Create: `tests/aircraft_agent/test_mission_worker.py`
- Create: `tests/fixtures/aircraft_mission_handshake.jsonl`

- [ ] Write deterministic fake-transport tests for normal upload, `MISSION_REQUEST` and `MISSION_REQUEST_INT`, repeated requests, timeout, denied ACK, and readback mismatch.
- [ ] Make one `MavlinkSession` own `/dev/ttyACM0` and dispatch messages to the active transaction without competing readers.
- [ ] Upload only after the complete wireless mission is staged and validated.
- [ ] Require accepted `MISSION_ACK`, then download and compare the mission before returning `VERIFIED`.
- [ ] Serialize mode, arm/disarm, mission, and readback transactions in one worker queue.
- [ ] Keep heartbeat/status telemetry collection non-destructive by publishing parsed messages from the same session dispatcher.
- [ ] Run all aircraft transaction tests and a recorded replay.
- [ ] Commit with `feat: verify aircraft missions on ELF`.

### Task 7: Rebuild Infrared Detection And GUIDED Tracking

**Files:**
- Create: `vision/config.py`
- Create: `vision/detector.py`
- Create: `vision/geometry.py`
- Create: `vision/guided_tracker.py`
- Create: `vision/mode_controller.py`
- Create: `vision/optical_state.py`
- Create: `tests/vision/test_detector.py`
- Create: `tests/vision/test_guided_tracker.py`
- Create: `tests/fixtures/ir_frames/`

- [ ] Preserve representative infrared frames from the existing test assets and write detector regression tests.
- [ ] Implement confidence, blob-area, acquire-count, loss-count, and timestamp outputs.
- [ ] Apply camera orientation `FORWARD` and configurable 0.04 m forward / 0.01 m right displacement.
- [ ] Implement bounded GUIDED horizontal corrections with low-pass filtering, deadband, acceleration limiting, and stale-frame suppression.
- [ ] Confirm the controller writes only in GUIDED and never changes mode on target loss.
- [ ] Replay recorded frames and plot error convergence for review.
- [ ] Commit with `refactor: isolate infrared guided tracking`.

### Task 8: Rebuild Precision Landing

**Files:**
- Create: `vision/precision_landing.py`
- Modify: `vision/mode_controller.py`
- Create: `tests/vision/test_precision_landing.py`
- Create: `docs/testing/precision-landing-procedure.md`

- [ ] Write tests for LAND/QLAND-only output, angular sign, camera offset, stale target rejection, and target-loss hysteresis.
- [ ] Emit `LANDING_TARGET` using the ArduPilot-supported frame and field validity for the configured estimator mode.
- [ ] Prohibit GUIDED velocity output while LAND precision landing is active.
- [ ] Record target angle, estimated height source, message rate, confidence, and final error for each landing test.
- [ ] Document propeller-off, tethered, staged-height, and final touchdown tests with abort criteria.
- [ ] Commit with `feat: restore precision landing state`.

### Task 9: Build Unified ELF Runtime And Services

**Files:**
- Create: `aircraft_agent/main.py`
- Create: `ops/systemd/low-altitude-aircraft.service`
- Create: `ops/systemd/low-altitude-vision.service`
- Create: `ops/install_aircraft.sh`
- Create: `ops/check_serial_roles.sh`
- Create: `ops/health_aircraft.sh`

- [ ] Fail startup if `/dev/ttyACM0`, `/dev/ttyUSB0`, or `/dev/ttyS9` identity is ambiguous or already owned.
- [ ] Start the aircraft link/mission service separately from the vision service with explicit restart limits.
- [ ] Expose atomic health snapshots for command ID, queue depth, FC heartbeat, optical state, and last error.
- [ ] Add log rotation and preserve transaction logs across service restarts.
- [ ] Deploy to ELF, verify serial ownership with `lsof`, and run protocol loopback before connecting the FC.
- [ ] Commit with `ops: install deterministic ELF services`.

### Task 10: Update RDK Network And Boot Services For `woshinailong`

**Files:**
- Create: `ops/systemd/low-altitude-rdk.service`
- Create: `ops/systemd/low-altitude-rdk-network.service`
- Create: `ops/configure_rdk_network.sh`
- Create: `ops/install_rdk.sh`
- Create: `ops/health_rdk.sh`
- Create: `docs/operations/ssh-recovery.md`

- [ ] Write a shell test or dry-run verifier for connection name, `ipv4.never-default`, L610 default route, and local aircraft host route.
- [ ] Replace the obsolete `mengchuang` SSID reference with `woshinailong` while keeping credentials outside Git.
- [ ] Make aircraft-link startup failure non-fatal to Rover/Tuya operation and visible in health.
- [ ] Preserve L610 as the cloud default route and verify `ip route get` for Tuya and the aircraft peer.
- [ ] Install an SSH recovery path that switches only the Wi-Fi profile and does not alter L610.
- [ ] Commit with `ops: configure RDK aircraft WiFi link`.

### Task 11: Refactor Tuya Cloud Ground Station

**Files:**
- Modify: `ground_station/server.js`
- Modify: `ground_station/public/index.html`
- Modify: `ground_station/public/app.js`
- Modify: `ground_station/public/style.css`
- Create: `ground_station/test/state.test.js`
- Create: `ground_station/test/mission.test.js`

- [ ] Preserve current OpenAPI signing, token refresh, property filtering, and cloud-only data source.
- [ ] Add vehicle route selection, aircraft altitude, mission path display, current item, queue clear, and transaction-stage timeline.
- [ ] Keep Rover and aircraft status visible side by side at common laptop viewport sizes.
- [ ] Display every aircraft message with its source receive time and do not compact duplicates.
- [ ] Clear aircraft detail fields and show `OPTICAL LINK BLOCKED` while blocked.
- [ ] Use browser geolocation only as a map fallback, never as vehicle navigation state.
- [ ] Run Node tests and Playwright checks at desktop and mobile widths; verify no clipping or overlap.
- [ ] Commit with `feat: show verified dual-vehicle missions`.

### Task 12: Add Parameter Audit And Reversible Configuration

**Files:**
- Create: `ops/backup_ardupilot_params.py`
- Create: `ops/apply_ardupilot_params.py`
- Create: `docs/operations/ardupilot-parameters.md`
- Create: `tests/ops/test_parameter_change_set.py`

- [ ] Back up full Rover and aircraft parameter sets before any write.
- [ ] Record each proposed parameter with vehicle, original value, new value, official ArduPilot URL, reason, and restore command.
- [ ] Refuse to apply a change set if the connected firmware/vehicle type does not match.
- [ ] Make no flight-controller parameter changes until bench evidence identifies a specific requirement.
- [ ] Commit with `ops: make ArduPilot changes reversible`.

### Task 13: End-to-End Fault Injection And Hardware Integration

**Files:**
- Create: `tests/integration/test_rdk_elf_link.py`
- Create: `tests/integration/test_end_to_end_mission.py`
- Create: `ops/demo_preflight.sh`
- Create: `docs/testing/integration-results.md`
- Create: `docs/operations/demo-runbook.md`

- [ ] Simulate dropped, duplicated, reordered, corrupted, and delayed wireless frames and verify eventual staging or typed failure.
- [ ] Test an aircraft mission from ground-station payload through RDK framing, ELF staging, fake FC upload, readback, and `VERIFIED`.
- [ ] Test optical lock loss during staging and during execution; verify no detailed state or command crosses while blocked.
- [ ] Deploy to RDK and ELF, run health scripts, and archive logs from one complete bench transaction.
- [ ] Verify Rover mission upload/readback without wheels and aircraft mission upload/readback without propellers.
- [ ] Perform staged outdoor/flight tests only with RC takeover and documented abort criteria.
- [ ] Commit with `test: prove complete competition workflow`.

### Task 14: Final Verification And Repository Delivery

**Files:**
- Modify: `TASKS.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/testing/integration-results.md`

- [ ] Run all Python, Node, protocol replay, shell lint, and browser checks.
- [ ] Run secret scan and verify no runtime state, credentials, or logs are tracked.
- [ ] Verify both devices use deployed commits and record their hashes.
- [ ] Run the demonstration preflight report and attach the result to test documentation.
- [ ] Push the complete branch to `Mrth1nk/Low-altitude-IoT-system`.
- [ ] Report verified capabilities separately from remaining flight-test risks.
