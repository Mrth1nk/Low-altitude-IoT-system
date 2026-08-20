# Rover WASD Combination Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the rover ground station continuously support combined WASD input without emitting a release/stop command.

**Architecture:** Add a pure key-composition helper to `public/core.js` so keyboard behavior can be unit tested. Replace the single `activeKey` frontend state with a set of held drive keys and keep the existing 250 ms command refresh mechanism.

**Tech Stack:** Browser JavaScript, Node.js built-in test runner.

---

### Task 1: Test key composition

**Files:**
- Modify: `ground_station/test/state.test.js`
- Modify: `ground_station/public/core.js`

- [ ] Add tests proving `W+A` combines forward and left, opposite keys cancel per axis, key case is normalized, and unrelated keys are ignored.
- [ ] Run `node --test ground_station/test/state.test.js` and verify the new tests fail because `composeDriveKeys` is missing.
- [ ] Implement `composeDriveKeys(keys)` returning `[steering, throttle]` or `null` when no effective axis is active.
- [ ] Run the focused test and verify it passes.

### Task 2: Integrate held-key control

**Files:**
- Modify: `ground_station/public/app.js`

- [ ] Replace `activeKey` with `heldDriveKeys`.
- [ ] On keydown, add the normalized WASD/arrow key, immediately send the recomputed combined command, and retain the 250 ms refresh.
- [ ] On keyup, remove the key; send the remaining combination immediately or only stop the refresh timer when no effective key remains.
- [ ] Preserve pointer-button behavior and do not call `stopDrive()` from keyboard release.
- [ ] Run `node --test ground_station/test/*.js` and verify the complete ground-station suite passes.
- [ ] Review the diff to confirm no backend, aircraft, or slave files changed.
