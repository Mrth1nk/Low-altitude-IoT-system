# Rover AUTO And Raw Position Design

## Scope

Repair Rover waypoint execution and ground-station position diagnostics without
changing manual drive behavior, aircraft transport, infrared tracking, or
precision landing.

## Observed Failures

- A real Tuya `auto` command reaches the RDK (`last_command=auto`) while the
  Rover flight mode remains `HOLD`.
- The RDK currently stores mission verification and execution readiness only in
  the active process. Readiness is evaluated at upload time and is not refreshed
  when the operator later requests AUTO.
- The mission worker sends `SET_MODE AUTO` without waiting for a matching Rover
  heartbeat, so a flight-controller rejection is not distinguishable from a
  successful request.
- The 480-byte compact state can evict Rover transaction status, GPS state, and
  failure text. The ground station therefore cannot explain an AUTO failure.
- Tuya raw state contains the real indoor `0,0` positions from both flight
  controllers, but the local ground station replaces them with previously valid
  coordinates.

## Required Behavior

### Position Reporting

- Publish the latest coordinates received from each flight controller, including
  `0,0` when that is the actual `GLOBAL_POSITION_INT` value.
- Add an explicit position-observed indicator so `0,0 received from flight
  controller` is distinguishable from `no position message received`.
- Round cloud coordinates to five decimal places when compacting is required.
- Display observed `0,0` in vehicle status metrics for indoor diagnostics.
- Continue treating `0,0` as invalid for map movement, waypoint construction,
  Home validation, and AUTO safety checks.
- Do not substitute a previous outdoor position over a newly observed `0,0`.

### Rover AUTO Execution

- Retain strict mission readback verification. AUTO must never execute an
  unverified or unsafe residual mission.
- On an AUTO request, refresh current Rover navigation telemetry and Home state
  instead of relying only on the snapshot captured during mission upload.
- Re-evaluate GPS fix, satellite count, nonzero location, Home, EKF flags,
  connection generation, and freshness at AUTO-request time.
- Confirm that the in-process verified mission still exists before AUTO. If the
  RDK service restarted, report that the mission must be uploaded again rather
  than trusting an unknown flight-controller mission.
- Send AUTO only after the refreshed gate passes.
- Wait for a Rover `HEARTBEAT` reporting `AUTO`; report success only after that
  confirmation. Preserve the observed mode in timeout or rejection errors.
- Keep the vehicle disarmed unless the operator separately issues ARM.

### Cloud State Priority

Within the Tuya 480-byte `rover_state` value, reserve space in this order:

1. timestamp, Rover mode/armed/link and observed position;
2. Rover GPS/Home/EKF execution gate and mission transaction stage/fault;
3. aircraft mode/armed/link and observed position;
4. one latest aircraft heartbeat;
5. optional secondary telemetry.

Command status must not evict either vehicle's observed coordinates. Optional
messages, packet counters, verbose identifiers, and diagnostic prose are removed
before core state.

## Components

- `rdk_agent/mavlink_rover.py`: refresh the execution gate and confirm AUTO by
  Rover heartbeat.
- `rdk_agent/rover_mission.py`: expose safe readiness re-evaluation for the
  already verified current-process mission.
- `rdk_agent/rover_state.py`: track position observation and produce a stable
  compact state with both vehicles and Rover AUTO failure details.
- `rdk_agent/tuya_rover_agent.py`: preserve aircraft position observation and
  route typed AUTO results into cloud status.
- `ground_station/server.js`: preserve observed zero coordinates instead of
  silently replacing them with stale outdoor values.
- `ground_station/public/core.js` and `app.js`: separate diagnostic coordinate
  display from navigation-coordinate validity.

## Error Handling

- Missing current-process verified mission: reject with `mission re-upload
  required`.
- Incomplete current navigation gate: reject with the exact missing and stale
  checks.
- AUTO heartbeat timeout: report requested AUTO and the last observed Rover
  mode.
- No position frame: show `-`; observed zero frame: show `0.00000, 0.00000`.
- Cloud payload must remain at or below 480 UTF-8 bytes in every fallback path.

## Verification

- Unit tests first for dynamic execution-gate refresh, heartbeat-confirmed AUTO,
  restart/re-upload rejection, observed zero positions, and 480-byte retention.
- JavaScript tests first for retaining newly observed zero coordinates while
  still rejecting zero as a mission/map coordinate.
- Run the complete Python and ground-station Node test suites.
- Deploy only the affected RDK files without changing networking or rebooting
  the board.
- Through the real Tuya path, verify:
  - indoor state displays both observed `0.00000, 0.00000` positions;
  - AUTO rejection includes an actionable reason when navigation is not ready;
  - after an outdoor mission upload with fresh GPS/Home/EKF, AUTO is reported
    successful only when the Rover heartbeat changes to AUTO.

