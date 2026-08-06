# Precision Landing Final-Descent Cutoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop camera-derived precision-landing corrections below a configurable 25 cm altitude while preserving the proven GUIDED tracker and all camera/link diagnostics.

**Architecture:** Add a LAND-only, altitude-valid final-descent latch to `VisionModeController`. The runtime supplies the threshold from the service environment and publishes the latch state; the precision-landing controller itself remains responsible only for constructing valid MAVLink targets.

**Tech Stack:** Python 3 dataclasses, `unittest`, OpenCV runtime, pymavlink, systemd.

---

### Task 1: Final-Descent State Machine

**Files:**
- Modify: `tests/vision/test_mode_controller.py`
- Modify: `vision/mode_controller.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that LAND emits targets above `0.25`, stops at and below
`0.25`, remains latched if altitude rises slightly, does not trigger on zero or
unknown altitude, resets outside LAND, and leaves GUIDED corrections unchanged.

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m unittest tests.vision.test_mode_controller -v
```

Expected: new final-descent assertions fail because no latch or threshold
exists.

- [ ] **Step 3: Implement the minimal latch**

Extend `VisionModeController.__init__` with
`final_descent_altitude_m: float = 0.25`, validate it is positive, and maintain
`_final_descent_active`. In `process`, clear the latch outside `LAND/QLAND`, set
it only for positive known altitude at or below the threshold, and skip the
precision-landing update while latched.

- [ ] **Step 4: Verify GREEN**

Run the mode-controller and precision-landing tests. Expected: all pass and
GUIDED behavior remains unchanged.

### Task 2: Runtime Configuration And Diagnostics

**Files:**
- Modify: `tests/vision/test_runtime.py`
- Modify: `vision/runtime.py`
- Modify: `ops/systemd/low-altitude-vision.service`

- [ ] **Step 1: Write failing runtime tests**

Test parsing of `PLND_FINAL_DESCENT_ALT_M=0.25`, diagnostic publication of
`final_descent_active` and `final_descent_altitude_m`, and overlay text
selection for active final descent.

- [ ] **Step 2: Verify RED**

```bash
python3 -m unittest tests.vision.test_runtime -v
```

Expected: missing configuration and diagnostic fields fail.

- [ ] **Step 3: Wire runtime state**

Construct `VisionModeController` with the environment threshold, add
`PLND_FINAL_DESCENT_ALT_M=0.25` to the service unit, publish latch diagnostics,
and append `FINAL DESCENT` to the preview overlay while active.

- [ ] **Step 4: Verify GREEN**

Run runtime and all vision tests. Expected: all pass.

### Task 3: Reversible ELF Deployment

**Files:**
- Deploy: `vision/mode_controller.py`
- Deploy: `vision/runtime.py`
- Deploy: `ops/systemd/low-altitude-vision.service`
- Update: `CHANGELOG.md`
- Update: `TASKS.md`

- [ ] **Step 1: Capture the remote restore point**

Back up `/opt/low-altitude-iot/current/vision`, the installed vision service,
and `/etc/low-altitude-iot/aircraft.env`; record SHA-256 hashes and the Git tag
`vision-working-before-final-descent-20260806`.

- [ ] **Step 2: Deploy without rebooting**

Copy only the two changed Python files and service unit, run `py_compile`, call
`systemctl daemon-reload`, and restart only `low-altitude-vision.service`.

- [ ] **Step 3: Verify the running service**

Confirm active state, zero restart loop, matching hashes, camera stream health,
optical snapshot updates, and no traceback or serial-ownership error.

- [ ] **Step 4: Run a propeller-off threshold check**

Use unit/injected state rather than altering live flight-controller altitude:
verify LAND target output above 25 cm and suppression below 25 cm, then verify
GUIDED correction remains available.

- [ ] **Step 5: Record results**

Document the backup path, deployed hashes, tests, and the remaining supervised
flight test. Do not claim touchdown improvement until physical flight verifies
it.

### Task 4: Final Verification

- [ ] Run:

```bash
python3 -m unittest discover -s tests -t . -v
git diff --check
```

Expected: complete suite passes with only the existing optional OpenCV skip.

- [ ] Confirm no RDK, aircraft-link, GUIDED gain, camera geometry, or flight
controller parameter changed as part of this implementation.
