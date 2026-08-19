# Dual-UAV Follow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a recoverable aircraft-2 maintenance-network command, stable zero-GPS display, simplified task UI, and an ArduPilot Follow pipeline that holds aircraft 2 five meters left of aircraft 1.

**Architecture:** The existing ELF `aircraft_agent` remains the only leader FC reader and generates MAVLink2 `FOLLOW_TARGET`. The rover RDK relays only validated target frames over the local aircraft network, and the existing aircraft-2 process injects them through its single-owner `MavlinkSession`. Cloud commands and state continue through the verified L610/Tuya coordinator path.

**Tech Stack:** Python 3.10, pymavlink, UDP, systemd, NetworkManager, Node.js, browser JavaScript, unittest, node:test.

---

## File Map

- Create `aircraft_agent/follow_target.py`: build and rate-limit leader `FOLLOW_TARGET` frames.
- Create `rdk_agent/follow_relay.py`: validate and relay target frames to aircraft 2.
- Create `slave_agent/follow_receiver.py`: receive validated frames and inject them via the existing MAVLink session.
- Create `ops/switch_slave_network.sh`: root-owned aircraft-2 network switch executor.
- Create `ops/systemd/low-altitude-slave-network-switch.service`: one-shot switch service.
- Create `ops/systemd/low-altitude-slave-network-switch.path`: root path watcher.
- Create `ops/configure_follow.py`: snapshot, validate, write, verify and restore Follow parameters.
- Modify `aircraft_agent/main.py`: subscribe publisher and drain generated frames into the existing link stream.
- Modify `rdk_agent/aircraft_transport.py`: expose raw MAVLink observer hook without adding a second UDP reader.
- Modify `rdk_agent/tuya_rover_agent.py`: construct and health-report the follow relay.
- Modify `shared_protocol/node_messages.py`: admit the typed aircraft-2 maintenance action and Follow mode.
- Modify `slave_agent/main.py`: construct follow receiver and network-request executor.
- Modify `slave_agent/command_queue.py`: route `follow` through normal verified mode handling and persist the maintenance request before ACK.
- Modify `slave_agent/state.py`: preserve zero coordinates and expose follow diagnostics.
- Modify `ground_station/public/index.html`: add aircraft-2 Follow control and remove transaction lamps.
- Modify `ground_station/public/app.js`: send Follow and stop rendering transaction lamps.
- Modify `ground_station/public/core.js`: format aircraft-2 zero coordinates without changing navigation validity.
- Modify `ops/install_aircraft.sh`, `ops/install_rdk.sh`, `ops/install_slave.sh`: install configuration and new systemd units atomically.
- Add focused tests under `tests/aircraft_agent`, `tests/rdk_agent`, `tests/slave_agent`, `tests/ops`, and `ground_station/test`.

## Task 1: Aircraft-2 Maintenance Network Command

**Files:**
- Modify: `shared_protocol/node_messages.py`
- Modify: `slave_agent/command_queue.py`
- Modify: `slave_agent/main.py`
- Create: `ops/switch_slave_network.sh`
- Create: `ops/systemd/low-altitude-slave-network-switch.service`
- Create: `ops/systemd/low-altitude-slave-network-switch.path`
- Modify: `ops/install_slave.sh`
- Test: `tests/shared_protocol/test_node_messages.py`
- Test: `tests/slave_agent/test_command_queue.py`
- Test: `tests/ops/test_slave_install.py`

- [ ] **Step 1: Write failing tests for typed network command and ACK ordering**

```python
def test_network_phone_is_a_valid_aircraft_2_command(self):
    wire = build_command_message(
        source="rover", target="aircraft_2", action="network_phone"
    )
    self.assertEqual(decode_node_message(wire)["payload"]["action"], "network_phone")

def test_network_request_is_persisted_before_verified_ack(self):
    request = FakeNetworkRequest()
    queue = make_queue(network_request=request)
    result = queue.accept(network_phone_message())
    self.assertTrue(request.persisted)
    self.assertEqual(result.stage, "VERIFIED")
```

- [ ] **Step 2: Run the focused tests and confirm rejection/failure**

Run: `PYTHONPATH=. python3 -m unittest tests.shared_protocol.test_node_messages tests.slave_agent.test_command_queue tests.ops.test_slave_install -v`

Expected: FAIL because `network_phone` is unsupported and no switch unit is installed.

- [ ] **Step 3: Implement typed request persistence and root path service**

Add `network_phone` to `COMMAND_ACTIONS`. Persist this exact request document atomically:

```json
{"mode":"phone","requested_at":0.0,"source":"aircraft_2"}
```

The root executor must validate `mode == phone`, remove the request, and run:

```bash
nmcli connection up "$PHONE_WIFI_PROFILE" ifname wlan0
```

The slave process writes `/run/low-altitude-slave/network-mode`; systemd.path starts the one-shot root service only after the queue has persisted and acknowledged the command.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command.

Expected: all focused tests PASS.

- [ ] **Step 5: Commit**

```bash
git add shared_protocol/node_messages.py slave_agent/command_queue.py slave_agent/main.py ops tests
git commit -m "feat: add slave maintenance network command"
```

## Task 2: Ground-Station Position and Task UI

**Files:**
- Modify: `ground_station/public/index.html`
- Modify: `ground_station/public/app.js`
- Modify: `ground_station/public/core.js`
- Modify: `ground_station/public/style.css`
- Test: `ground_station/test/state.test.js`
- Test: `ground_station/test/mission.test.js`

- [ ] **Step 1: Write failing UI-model tests**

```javascript
test("slave zero position is displayed but not navigation-valid", () => {
  const slave = {lat: 0, lng: 0, position_observed: false};
  assert.equal(formatVehiclePosition(slave, "aircraft_2"), "0.00000, 0.00000");
  assert.equal(validObservedPosition(slave), false);
});

test("transaction timeline is absent from every vehicle panel", () => {
  assert.equal(renderTransactionTimeline({}), "");
});
```

- [ ] **Step 2: Run tests and confirm current behavior fails**

Run: `node --test ground_station/test/state.test.js ground_station/test/mission.test.js`

Expected: FAIL because aircraft 2 formats invalid position as `-` and transaction lamps still render.

- [ ] **Step 3: Implement the UI behavior**

Use five decimal places for aircraft 2 state-card coordinates, but keep map and mission gates dependent on `position_observed === true`. Remove the transaction row markup and its render/update calls for rover, aircraft 1 and aircraft 2. Do not remove task messages or operation logs.

- [ ] **Step 4: Run all Node tests**

Run: `node --test ground_station/test/*.test.js`

Expected: all Node tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ground_station/public ground_station/test
git commit -m "fix: simplify mission state and preserve zero position"
```

## Task 3: Leader FOLLOW_TARGET Publisher

**Files:**
- Create: `aircraft_agent/follow_target.py`
- Modify: `aircraft_agent/main.py`
- Test: `tests/aircraft_agent/test_follow_target.py`

- [ ] **Step 1: Write failing publisher tests**

```python
def test_builds_follow_target_from_fresh_leader_state(self):
    publisher = FollowTargetPublisher(encoder=fake_encoder, clock=clock)
    publisher.observe(global_position(lat=321193000, lon=1189530000, alt=123400))
    publisher.observe(attitude(yaw=1.0))
    frame = publisher.next_frame()
    self.assertEqual(frame.message_id, 144)
    self.assertEqual(frame.source_system, 1)
    self.assertEqual(frame.lat, 321193000)

def test_stale_position_stops_output(self):
    publisher.observe(global_position())
    clock.advance(1.51)
    self.assertIsNone(publisher.next_frame())
```

- [ ] **Step 2: Run the test and confirm the module is absent**

Run: `PYTHONPATH=. python3 -m unittest tests.aircraft_agent.test_follow_target -v`

Expected: FAIL with missing `aircraft_agent.follow_target`.

- [ ] **Step 3: Implement bounded publisher**

Construct a dedicated pymavlink MAVLink2 encoder with `srcSystem=1` and `srcComponent=1`. Use the ELF-installed signature:

```python
mav.follow_target_send(
    timestamp_ms,
    capabilities,
    lat_e7,
    lon_e7,
    altitude_msl_m,
    (vx, vy, vz),
    (0.0, 0.0, 0.0),
    quaternion,
    (roll_rate, pitch_rate, yaw_rate),
    (0.0,) * 3,
    0,
)
```

Rate-limit to 10 Hz, require leader system ID 1, fresh heartbeat and position no older than 1.5 seconds, and expose counters for produced/stale/dropped frames. Feed output into the existing `MavlinkTelemetryForwarder`; do not open another serial device.

- [ ] **Step 4: Run publisher and existing aircraft tests**

Run: `PYTHONPATH=. python3 -m unittest discover -s tests/aircraft_agent -t . -v`

Expected: all aircraft-agent tests PASS.

- [ ] **Step 5: Commit**

```bash
git add aircraft_agent tests/aircraft_agent
git commit -m "feat: publish leader follow target"
```

## Task 4: Rover Follow Relay

**Files:**
- Create: `rdk_agent/follow_relay.py`
- Modify: `rdk_agent/aircraft_transport.py`
- Modify: `rdk_agent/tuya_rover_agent.py`
- Test: `tests/rdk_agent/test_follow_relay.py`

- [ ] **Step 1: Write failing validation and stale tests**

```python
def test_relays_only_follow_target_from_system_one(self):
    relay.observe(mavlink2_follow_target(source_system=1))
    self.assertEqual(sock.sent[0][1], ("192.168.4.3", 14630))

def test_rejects_wrong_message_source_and_stale_target(self):
    relay.observe(mavlink2_follow_target(source_system=2))
    self.assertEqual(sock.sent, [])
```

- [ ] **Step 2: Run focused test and confirm module is absent**

Run: `PYTHONPATH=. python3 -m unittest tests.rdk_agent.test_follow_relay -v`

Expected: FAIL with missing `rdk_agent.follow_relay`.

- [ ] **Step 3: Implement raw-frame observer and relay**

Parse MAVLink1/2 headers without opening a second receive socket. Accept only message ID `144`, source system `1`, well-formed complete frames, and datagrams observed within 1.5 seconds. Send to `SLAVE_FOLLOW_IP=192.168.4.3`, `SLAVE_FOLLOW_PORT=14630`. Add sent/rejected/last-frame metrics to RDK health only; do not put raw frames in Tuya properties.

- [ ] **Step 4: Run RDK tests**

Run: `PYTHONPATH=. python3 -m unittest discover -s tests/rdk_agent -t . -v`

Expected: all RDK tests PASS.

- [ ] **Step 5: Commit**

```bash
git add rdk_agent tests/rdk_agent
git commit -m "feat: relay leader follow target"
```

## Task 5: Follower Target Receiver and FOLLOW Command

**Files:**
- Create: `slave_agent/follow_receiver.py`
- Modify: `slave_agent/main.py`
- Modify: `shared_protocol/node_messages.py`
- Modify: `ground_station/public/index.html`
- Modify: `ground_station/public/app.js`
- Test: `tests/slave_agent/test_follow_receiver.py`
- Test: `tests/slave_agent/test_command_queue.py`
- Test: `ground_station/test/mission.test.js`

- [ ] **Step 1: Write failing single-owner and Follow command tests**

```python
def test_receiver_injects_valid_frame_through_existing_session(self):
    receiver = FollowTargetReceiver(session=session, sock=sock)
    receiver.run_once()
    self.assertEqual(session.raw_writes, [valid_follow_frame])

def test_follow_action_uses_verified_mode_change(self):
    result = operations.set_mode("FOLLOW")
    self.assertTrue(result["verified"])
```

```javascript
test("aircraft 2 exposes FOLLOW command", () => {
  assert.deepEqual(buildVehicleCommand("follow", "aircraft_2"), {
    target: "aircraft_2", command: "aircraft_follow"
  });
});
```

- [ ] **Step 2: Run focused tests and confirm failures**

Run: `PYTHONPATH=. python3 -m unittest tests.slave_agent.test_follow_receiver tests.slave_agent.test_command_queue -v && node --test ground_station/test/mission.test.js`

Expected: FAIL because receiver and Follow action do not exist.

- [ ] **Step 3: Implement receiver and UI command**

Bind UDP `14630`, accept only rover peer `192.168.4.2`, message ID `144`, source system `1`, and complete MAVLink2 frames. Call `session.write_raw(frame)`; never create another pymavlink serial connection. Add `follow` to typed command actions and render the button only for aircraft 2.

- [ ] **Step 4: Run focused and full tests**

Run: `PYTHONPATH=. python3 -m unittest discover -s tests -t . -v && node --test ground_station/test/*.test.js`

Expected: all Python and Node tests PASS.

- [ ] **Step 5: Commit**

```bash
git add slave_agent shared_protocol ground_station tests
git commit -m "feat: inject follow targets into follower"
```

## Task 6: Follow Parameter Snapshot and Restore Tool

**Files:**
- Create: `ops/configure_follow.py`
- Modify: `ops/install_slave.sh`
- Test: `tests/ops/test_configure_follow.py`

- [ ] **Step 1: Write failing dry-run and restore tests**

```python
def test_snapshot_contains_every_modified_parameter(self):
    result = configure(fake_fc, apply=False)
    self.assertEqual(set(result.snapshot), {
        "SYSID_THISMAV", "FOLL_ENABLE", "FOLL_SYSID", "FOLL_DIST_MAX",
        "FOLL_OFS_TYPE", "FOLL_OFS_X", "FOLL_OFS_Y", "FOLL_OFS_Z",
        "FOLL_ALT_TYPE",
    })

def test_restore_script_replays_original_values(self):
    self.assertIn("param_set_send", generated_restore_script())
```

- [ ] **Step 2: Run tests and confirm tool is absent**

Run: `PYTHONPATH=. python3 -m unittest tests.ops.test_configure_follow -v`

Expected: FAIL with missing configure tool.

- [ ] **Step 3: Implement explicit snapshot/apply/verify/restore modes**

The apply values are exactly:

```python
FOLLOW_VALUES = {
    "SYSID_THISMAV": 2,
    "FOLL_ENABLE": 1,
    "FOLL_SYSID": 1,
    "FOLL_DIST_MAX": 30,
    "FOLL_OFS_TYPE": 1,
    "FOLL_OFS_X": 0,
    "FOLL_OFS_Y": -5,
    "FOLL_OFS_Z": 0,
    "FOLL_ALT_TYPE": 1,
}
```

Reject non-Copter heartbeat, missing parameters, armed state, wrong serial
identity, and failed readback. Save timestamped JSON and an executable restore
script under `/var/backups/low-altitude-iot/follow/`.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. python3 -m unittest tests.ops.test_configure_follow -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/configure_follow.py ops/install_slave.sh tests/ops/test_configure_follow.py
git commit -m "feat: configure recoverable follow parameters"
```

## Task 7: Atomic Deployment and No-Propeller Verification

**Files:**
- Modify: `ops/install_aircraft.sh`
- Modify: `ops/install_rdk.sh`
- Modify: `ops/install_slave.sh`
- Modify: `ops/health_aircraft.sh`
- Modify: `ops/health_rdk.sh`
- Modify: `ops/health_slave.sh`
- Modify: `docs/operations/slave-aircraft-deployment.md`
- Modify: `CHANGELOG.md`
- Modify: `TASKS.md`

- [ ] **Step 1: Run full local verification before deployment**

Run:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -t . -v
node --test ground_station/test/*.test.js
python3 -m compileall -q aircraft_agent rdk_agent slave_agent shared_protocol tests
git diff --check
```

Expected: zero failures.

- [ ] **Step 2: Deploy ELF, rover and follower atomically**

Use the existing install scripts so each host receives a timestamped backup and
rollback target. Do not reboot any board. Restart only the affected service
after its preflight passes.

- [ ] **Step 3: Snapshot and apply follower parameters**

First run `configure_follow.py --snapshot --verify-only`. Confirm disarmed
Copter heartbeat and show the saved snapshot path. Then run `--apply`, re-read
all nine parameters, and generate the restore command. Never send ARM or mode
change.

- [ ] **Step 4: Verify the complete no-propeller path on `woshinailong`**

Confirm:

```text
leader publisher: FOLLOW_TARGET source=1, approximately 10 Hz
rover relay: receiving and forwarding msgid 144
follower receiver: accepting source=1 and writing through the sole session
follower FC: FOLLOW_TARGET visible in MAVLink Inspector
stale test: target stream stops within 1.5 seconds
deployment: no ARM and no FOLLOW mode command
```

- [ ] **Step 5: Verify maintenance-network command**

Send `aircraft_2_network_phone`, confirm ACK was emitted before network loss,
SSH through the phone hotspot, then manually restore `woshinailong` and confirm
the follower returns online.

- [ ] **Step 6: Update docs and run final verification**

Record backup paths, parameter readback, message frequency, serial owners,
remaining outdoor test and restore commands. Re-run the Step 1 commands.

- [ ] **Step 7: Commit and tag only after evidence is complete**

```bash
git add ops docs CHANGELOG.md TASKS.md
git commit -m "docs: record dual-uav follow deployment"
git tag -a national-finals-dual-uav-follow-20260819 -m "No-propeller verified dual-UAV follow release"
```

Do not push or tag if any hardware acceptance item is unverified.
