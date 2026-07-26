# 红外追踪与精准降落恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore the proven pixel-based GUIDED tracking and angle-only LAND precision-landing behavior while preserving the current optical gate and UART9 ownership.

**Architecture:** Keep `vision.runtime` as the only owner of UART9 and the camera. Move the reference script's pixel control math into small pure functions, use those functions from `VisionModeController`, and keep `PrecisionLandingController` as the LAND-only MAVLink message builder. No mode changes, arming, parameter writes, or board reboot.

**Tech Stack:** Python 3, OpenCV, NumPy, pymavlink, unittest.

---

### Task 1: Add failing regression tests for reference behavior

**Files:**
- Modify: `tests/vision/test_guided_tracker.py`
- Modify: `tests/vision/test_precision_landing.py`

- [ ] **Step 1: Add a GUIDED sign and altitude-independence test**

Add a test that feeds the same image-space target at two different altitudes and asserts the forward correction remains the reference sign and magnitude:

```python
def test_reference_guided_pixel_math_does_not_scale_with_altitude(self):
    correction_low = self.controller.process(
        self.gray_with_target(320, 190),
        mode="GUIDED",
        altitude_m=1.0,
        altitude_source="relative_altitude",
        timestamp=1.0,
        now=1.0,
    ).correction
    correction_high = self.controller.process(
        self.gray_with_target(320, 190),
        mode="GUIDED",
        altitude_m=8.0,
        altitude_source="relative_altitude",
        timestamp=1.1,
        now=1.1,
    ).correction
    self.assertLess(correction_low.forward_mps, 0.0)
    self.assertAlmostEqual(
        correction_low.forward_mps,
        correction_high.forward_mps,
        places=3,
    )
```

- [ ] **Step 2: Add a LAND angle-only test without altitude**

Add a test that provides a fresh target while `altitude_m=0.0` and `altitude_source="unknown"` and asserts a `LANDING_TARGET` is emitted after acquisition with `position_valid == 0`.

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```bash
python3 -m unittest tests.vision.test_guided_tracker tests.vision.test_precision_landing -v
```

Expected: the new GUIDED test fails because the current body-offset transform reverses the reference forward sign and scales with altitude; the LAND test fails because the current precision controller rejects unknown altitude.

### Task 2: Implement the smallest reference-compatible control change

**Files:**
- Modify: `vision/config.py`
- Modify: `vision/guided_tracker.py`
- Modify: `vision/mode_controller.py`
- Modify: `vision/precision_landing.py`
- Modify: `vision/runtime.py`

- [ ] **Step 1: Add reference pixel-control configuration**

Add `pixel_dead_zone`, `pixel_gain_forward`, `pixel_gain_right`, and `pixel_max_speed_mps` to `TrackerConfig`, defaulting to `25`, `0.6`, `0.6`, and `0.35`.

- [ ] **Step 2: Add a pure pixel correction method**

Implement `GuidedTracker.update_pixels(center_x, center_y, frame_width, frame_height, ...)` with:

```python
norm_x = (center_x - frame_width / 2.0) / max(1.0, frame_width / 2.0)
norm_y = (center_y - frame_height / 2.0) / max(1.0, frame_height / 2.0)
forward = 0.0 if abs(center_y - frame_height / 2.0) <= pixel_dead_zone else pixel_gain_forward * norm_y
right = 0.0 if abs(center_x - frame_width / 2.0) <= pixel_dead_zone else pixel_gain_right * norm_x
```

Apply symmetric speed limiting and retain stale-frame and GUIDED mode gates.

- [ ] **Step 3: Use pixel correction for GUIDED**

In `VisionModeController.process`, call `update_pixels` for fresh locked detections in GUIDED mode. Do not require `altitude_m > 0` for this path. Keep the existing optical lock and stale-frame gate.

- [ ] **Step 4: Remove altitude as a LAND send prerequisite**

In `PrecisionLandingController.update`, accept fresh locked detection in LAND/QLAND even when altitude is unknown. Calculate angle from normalized pixel geometry, use `BODY_FRD`, and leave `position_valid=0`. Apply the known camera offset only when a positive, fresh altitude is available; otherwise use zero offset rather than inventing distance.

- [ ] **Step 5: Configure camera capture explicitly and publish diagnostics**

After opening the camera in `vision.runtime`, set width, height, and buffer size to `640`, `480`, and `1`. Add actual frame width/height, target center, target pixel offsets, and last control kind to the atomic optical state.

### Task 3: Verify locally and on the aircraft without arming

**Files:**
- No new production files.
- Read: `/etc/low-altitude-iot/aircraft.env` and `low-altitude-vision.service` on the aircraft.

- [ ] **Step 1: Run focused vision tests**

Run the two focused test modules and then:

```bash
python3 -m unittest discover -s tests/vision -t . -v
```

- [ ] **Step 2: Deploy only the vision package**

Copy the changed `vision/*.py` files to a new release directory on the aircraft, update the `current` symlink atomically, and restart only `low-altitude-vision.service`. Do not reboot the board, change network profiles, change the aircraft bridge, or modify parameters.

- [ ] **Step 3: Verify UART9 bidirectionally**

Run the harmless `MAV_CMD_REQUEST_MESSAGE(AUTOPILOT_VERSION)` probe already used in diagnosis and require an `AUTOPILOT_VERSION` response.

- [ ] **Step 4: Verify static lock and mode-gated counters**

Read `/run/low-altitude-iot/optical-state.json` and the preview. With the aircraft disarmed:

- STABILIZE: `locked=true`, `guided_tx_count` and `landing_tx_count` unchanged.
- GUIDED: `guided_tx_count` increases and `last_control.kind=guided_velocity`.
- LAND: `landing_tx_count` increases and `last_control.kind=landing_target`.
- Remove the light target: counters stop increasing and `blocked=true`.

No claim of flight success is made until the operator performs an outdoor, prop-safe test with GPS/Home and the remote controller ready.
