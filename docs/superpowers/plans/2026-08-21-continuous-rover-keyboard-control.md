# Continuous Rover Keyboard Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make held WASD/arrow input produce continuous, combined rover control without overlapping Tuya requests or repeated flight-mode changes.

**Architecture:** A testable browser-side command pump owns serialization, state coalescing, keepalive timing, and neutral release. The RDK rover adapter caches whether a manual-capable mode has already been confirmed and sends subsequent RC overrides directly. The existing RDK timeout remains the independent failsafe.

**Tech Stack:** Browser JavaScript, Node.js built-in test runner, Python `unittest`, pymavlink, Tuya Cloud API.

---

### Task 1: Serialize browser drive commands

**Files:**
- Modify: `ground_station/public/core.js`
- Modify: `ground_station/test/mission.test.js`

- [ ] **Step 1: Write failing tests**

Add tests using a deferred Promise and fake timer hooks. Assert that only one
send is active, intermediate vectors are coalesced to the newest vector, a full
release sends `{command: "manual", steering: 0, throttle: 0}`, and an unchanged
non-neutral vector schedules a keepalive.

- [ ] **Step 2: Verify the tests fail**

Run: `node --test ground_station/test/mission.test.js`

Expected: failure because `createDriveCommandPump` is not exported.

- [ ] **Step 3: Add the command pump**

Implement and export:

```js
function createDriveCommandPump(send, {
  keepaliveMs = 700,
  now = () => Date.now(),
  schedule = setTimeout,
  cancel = clearTimeout,
} = {}) {
  // Keep one request in flight, retain only the latest desired vector,
  // send neutral once on release, and schedule non-neutral keepalives.
}
```

- [ ] **Step 4: Verify the focused tests pass**

Run: `node --test ground_station/test/mission.test.js`

Expected: all tests pass.

### Task 2: Route keyboard and pointer state through the pump

**Files:**
- Modify: `ground_station/public/app.js`

- [ ] **Step 1: Replace interval-based drive state**

Create one pump around `postCommand`. `startDrivePayload` calls `update`; key or
pointer release calls `update(0, 0)` when no drive vector remains. Remove the
250 ms `setInterval` path.

- [ ] **Step 2: Add focus-loss neutralization**

On `window.blur` and `document.visibilitychange` to hidden, clear held keys and
queue one neutral manual command.

- [ ] **Step 3: Run JavaScript tests and syntax check**

Run:

```bash
node --check ground_station/public/app.js
node --test ground_station/test/*.test.js
```

Expected: syntax succeeds and all tests pass.

### Task 3: Avoid repeated MANUAL mode transitions on RDK

**Files:**
- Modify: `rdk_agent/mavlink_rover.py`
- Modify: `rdk_agent/tests/test_mavlink_rover.py`

- [ ] **Step 1: Write the failing test**

Call `manual()` twice with a fake `set_mode` and `rc_override`. Assert mode setup
runs once while both RC override values are sent. Then simulate a heartbeat in
a different mode and assert the next manual command performs mode setup again.

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m unittest rdk_agent.tests.test_mavlink_rover -v`

Expected: repeated `set_mode` calls violate the new assertion.

- [ ] **Step 3: Implement manual-mode readiness**

Add `_manual_mode_ready`, reset it on connection close and when telemetry sees a
non-manual mode. `manual()` confirms MANUAL/HOLD only when readiness is false,
then sends RC override directly on every call.

- [ ] **Step 4: Verify focused and complete tests**

Run:

```bash
python3 -m unittest rdk_agent.tests.test_mavlink_rover -v
python3 -m unittest discover -s tests -t . -v
node --test ground_station/test/*.test.js
```

Expected: all tests pass.

### Task 4: Deploy and verify without board reboot

**Files:**
- Deploy: `ground_station/public/core.js`
- Deploy: `ground_station/public/app.js`
- Deploy: `rdk_agent/mavlink_rover.py`

- [ ] **Step 1: Restart the local ground-station launch agent**

Restart `com.low-altitude-iot.ground-station` and verify `/api/state` responds.

- [ ] **Step 2: Switch the main RDK to the phone hotspot**

Issue `network_phone`, SSH to `sunrise@192.168.43.175`, back up the active file,
deploy `mavlink_rover.py`, and restart only `low-altitude-rdk.service`.

- [ ] **Step 3: Switch the main RDK back to aircraft Wi-Fi**

Issue `network_aircraft`; verify Tuya state remains fresh and L610 remains the
cloud route.

- [ ] **Step 4: Perform a safe command-path verification**

Send neutral manual control and verify `last_command=manual`, zero steering and
zero throttle. Physical held-key continuity still requires wheels raised or a
clear test area with the operator present.
