# Dual-Aircraft RDK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Add an RDK-X5 slave aircraft as `aircraft_2` without changing the stable rover-to-cloud or ELF main-aircraft behavior.

**Architecture:** The rover RDK remains the only Tuya/L610 gateway. It keeps the existing `aircraft_1` transport and adds a separate typed UDP peer for `aircraft_2` on ports 14610/14620. The slave RDK owns one local Copter MAVLink session, queues commands and complete missions, and reports compact state back to the rover. Tuya reports `rover_state` and `slave_state` together every two seconds. The browser keeps independent aircraft state, history, controls, and mission queues.

**Tech Stack:** Python 3, pymavlink, UDP/JSON protocol, systemd, Node.js, browser JavaScript, TuyaLink/OpenAPI, unittest and node:test.

---

## Task 1: Shared slave-node protocol

**Files:**
- Create: `shared_protocol/node_messages.py`
- Test: `tests/shared_protocol/test_node_messages.py`

1. Write failing tests for required `source`, `target`, `command_id`, `sequence`, timestamp and message type fields; reject wrong targets, invalid types and oversized datagrams.
2. Run `python3 -m unittest tests.shared_protocol.test_node_messages -v` and confirm the missing module failure.
3. Implement canonical compact JSON encoding/decoding, SHA-256 mission digest, command/status/ACK constructors and strict validation.
4. Re-run the focused test, then all `tests/shared_protocol` tests.
5. Commit as `feat: add typed slave node protocol`.

## Task 2: Slave RDK aircraft service

**Files:**
- Create: `slave_agent/__init__.py`
- Create: `slave_agent/main.py`
- Create: `slave_agent/link.py`
- Create: `slave_agent/command_queue.py`
- Create: `slave_agent/state.py`
- Create: `slave_agent/optical_gate.py`
- Create: `ops/install_slave.sh`
- Create: `ops/health_slave.sh`
- Create: `ops/systemd/low-altitude-slave.service`
- Test: `tests/slave_agent/test_link.py`
- Test: `tests/slave_agent/test_command_queue.py`
- Test: `tests/slave_agent/test_state.py`
- Test: `tests/ops/test_slave_install.py`

1. Write failing tests for UDP peer filtering, 1 Hz state output, three-second offline handling, bounded queue, command deduplication, expired-command rejection and default `blocked=false` gate.
2. Write failing tests proving mode/arm commands call the existing `aircraft_agent.MavlinkSession` path and mission fragments are persisted, digest-checked and handed to the existing mission worker only after commit.
3. Implement one event loop with a nonblocking UDP receiver, bounded worker queue and one exclusive local MAVLink session; do not forward raw MAVLink over UDP.
4. Add root-owned environment configuration for node ID, peer, ports and FC by-id. Add install-time checks for fixed by-id, Copter heartbeat and absence of competing serial owners.
5. Run focused slave and installer tests, then the full Python suite.
6. Commit as `feat: add RDK slave aircraft service`.

## Task 3: Rover coordinator and compact cloud state

**Files:**
- Create: `rdk_agent/slave_transport.py`
- Modify: `rdk_agent/command_router.py`
- Modify: `rdk_agent/rover_state.py`
- Modify: `rdk_agent/tuya_rover_agent.py`
- Modify: `rdk_agent/start_rover_stack.sh`
- Modify: `rdk_agent/rdk.env.example`
- Modify: `ops/install_rdk.sh`
- Modify: `ops/health_rdk.sh`
- Test: `tests/rdk_agent/test_slave_transport.py`
- Test: `tests/rdk_agent/test_command_router.py`
- Test: `rdk_agent/tests/test_rover_state.py`
- Test: `tests/integration/test_dual_aircraft_link.py`

1. Write failing tests for routing `aircraft_2`, preserving `aircraft` as an alias for `aircraft_1`, independent peer/transaction state, retries and ACK deduplication.
2. Write failing tests that `slave_state` remains at most 480 UTF-8 bytes under long fault/event data and that one Tuya report contains both state properties while retaining the existing report interval.
3. Implement `SlaveTransport` on local port 14610/peer port 14620, wire it into the runtime router and isolate its failures from rover and main-aircraft paths.
4. Add compact slave-state generation and include it beside unchanged `rover_state` in the existing property report.
5. Extend health/install scripts without stopping or replacing the existing ELF services.
6. Run focused router/state/integration tests, then the complete Python suite.
7. Commit as `feat: coordinate slave aircraft through rover RDK`.

## Task 4: Cloud ground station dual-aircraft UI

**Files:**
- Modify: `ground_station/server.js`
- Modify: `ground_station/public/core.js`
- Modify: `ground_station/public/app.js`
- Modify: `ground_station/public/index.html`
- Modify: `ground_station/public/style.css`
- Modify: `ground_station/test/state.test.js`
- Modify: `ground_station/test/state-merge.test.js`
- Modify: `ground_station/test/state-reader.test.js`
- Modify: `ground_station/test/mission.test.js`

1. Write failing node tests proving one state read parses and independently caches `rover_state` and `slave_state`, including partial/missing updates.
2. Write failing tests for `主机 1`/`从机 2` target isolation, `aircraft_2` command payloads, separate message histories and separate mission queues.
3. Extend the map selector to 小车/主机/从机, preserving existing main behavior and aircraft return-point logic for each aircraft queue.
4. Add aircraft tabs, always-visible online status, selected-node controls and compact responsive layout; disable only the stale/offline selected node.
5. Run `node --test ground_station/test/*.test.js` and browser-level smoke checks at desktop and laptop viewports.
6. Commit as `feat: add slave aircraft ground station controls`.

## Task 5: Documentation and operations

**Files:**
- Modify: `AGENTS.md`
- Modify: `TASKS.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/operations/demo-runbook.md`
- Create: `docs/operations/slave-aircraft-deployment.md`

1. Document fixed IP `192.168.4.3`, ports 14610/14620, environment variables, service names, health commands and rollback commands without recording secrets.
2. Document that slave optical gating is reserved but disabled, and that OFFLINE and BLOCKED remain separate states.
3. Record the single-aircraft baseline tag and dual-aircraft rollback procedure.
4. Run shell syntax checks and documentation path checks.
5. Commit as `docs: add dual-aircraft deployment runbook`.

## Task 6: Device and cloud deployment

**Files:**
- Runtime only: rover `/opt/low-altitude-iot/current`
- Runtime only: slave `/opt/low-altitude-iot/current`
- Runtime only: Tuya product property `slave_state`

1. Back up both device runtimes and unit/environment files; do not restart either board.
2. Deploy the slave service over its phone-hotspot SSH, configure a high-priority `woshinailong` profile with static `192.168.4.3`, and keep a tested phone-hotspot recovery profile.
3. Deploy rover changes while preserving L610 default routing and the existing main-aircraft transport.
4. Add Tuya string property `slave_state` with a 480-byte-compatible limit and verify it appears in the same property report as `rover_state`.
5. Switch both RDKs to `woshinailong`; verify 1 Hz slave heartbeat, mode changes, ARM/DISARM ACK, mission upload/readback and no interruption when a rover or main command is sent. Keep propellers removed.
6. Verify stale slave link becomes OFFLINE after three seconds and recovers without replaying old commands.
7. Run complete Python and Node test suites, installer dry runs, service health checks and ground-station browser screenshots.
8. Commit any deployment-safe fixes, push the branch, and create/push an annotated dual-aircraft recovery tag only after all verification succeeds.
