# Rover Mission Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Rover mission upload bounded, asynchronous, failure-safe, fully verified, and gated by fresh current-connection navigation state.

**Architecture:** Add a single asynchronous mission worker around the existing synchronous protocol manager. Keep `RoverMavlink` as the sole connection owner with a session lock, and make the Tuya loop submit and poll jobs by command ID.

**Tech Stack:** Python 3.10, threading, queue, pymavlink, unittest.

---

### Task 1: Bound And Safeguard Mission Transactions

**Files:**
- Modify: `rdk_agent/rover_mission.py`
- Modify: `tests/rdk_agent/test_rover_mission.py`

- [ ] Write failing fake-transport tests for an operation deadline, repeated
  sequence exhaustion, 101-item preflight rejection, cancellation/clear on
  failure, and param/autocontinue mismatch.
- [ ] Run `python3 -m unittest tests.rdk_agent.test_rover_mission -v` and verify
  the new tests fail for the intended missing behavior.
- [ ] Add one non-resetting deadline, per-sequence counters, maximum 100
  executable items, safe cancellation/clear, and complete readback comparison.
- [ ] Re-run the mission tests and verify they pass.

### Task 2: Track Connection Generation And Freshness

**Files:**
- Modify: `rdk_agent/mavlink_rover.py`
- Modify: `rdk_agent/rover_mission.py`
- Modify: `tests/rdk_agent/test_rover_mission.py`

- [ ] Write failing tests that reject stale GPS/location/EKF/Home and reject
  telemetry from an older connection generation.
- [ ] Run the focused tests and verify the expected failures.
- [ ] Timestamp each navigation source, increment generation on reconnect,
  clear Home on disconnect, and require fresh current-generation data.
- [ ] Re-run focused tests and existing manual RC tests.

### Task 3: Add Asynchronous Worker And Typed Receipts

**Files:**
- Modify: `rdk_agent/rover_mission.py`
- Modify: `rdk_agent/mavlink_rover.py`
- Modify: `rdk_agent/tuya_rover_agent.py`
- Modify: `tests/rdk_agent/test_rover_mission.py`
- Create: `tests/rdk_agent/test_rover_mission_runtime.py`

- [ ] Write failing tests for immediate queue return, responsive loop ticks
  during repeated requests, one active MAVLink owner, result polling by
  `command_id`, and typed failed receipts.
- [ ] Run focused tests and verify each new behavior fails.
- [ ] Implement `RoverMissionWorker`, shared session locking, asynchronous
  submit/poll, and runtime receipt conversion.
- [ ] Run focused tests, the root suite, and the RDK baseline.

### Task 4: Verify On Actual RDK And Commit

**Files:**
- Modify only files from Tasks 1-3.

- [ ] Copy the repository test snapshot to an explicit `/tmp` directory on the
  RDK without touching the deployed service path.
- [ ] Run the focused and root-compatible tests with RDK Python 3.10.
- [ ] Confirm no service restart or parameter command occurred.
- [ ] Run `git diff --check`, compile changed Python files, and commit the
  follow-up.
