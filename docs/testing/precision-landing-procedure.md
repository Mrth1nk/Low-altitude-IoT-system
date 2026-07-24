# Precision Landing Test Procedure

## Scope and protocol

This procedure validates infrared precision landing with the camera mounted
`FORWARD`. The software uses the shared Task 7 bright-spot detector and camera
geometry. It compensates the measured camera installation offset:

- camera: `+0.04 m` forward of the vehicle origin
- camera: `+0.01 m` right of the vehicle origin
- body axes: forward-right-down (FRD)
- positive `angle_x`: target is to vehicle right
- negative `angle_y`: target is forward of the vehicle

The output follows the ArduPilot MAVLink precision-landing interface:

- message: MAVLink 2 `LANDING_TARGET`
- frame: `MAV_FRAME_BODY_FRD` (`12`)
- `PLND_EST_TYPE=0`: angle-only/raw-sensor input
- `angle_x` and `angle_y`: populated
- `distance`: slant distance derived from the named altitude source
- `position_valid=0`
- `x`, `y`, `z`, `q`, and `type`: zero and not claimed as valid
- nominal message rate: `10 Hz`, never below the ArduPilot minimum of `1 Hz`

References:

- <https://ardupilot.org/dev/docs/mavlink-precision-landing.html>
- <https://ardupilot.org/copter/docs/precision-landing-and-loiter.html>
- <https://mavlink.io/en/services/landing_target.html>

This task does **not** change flight-controller parameters. Before testing,
record the current parameter file and verify the existing setup includes
`PLND_ENABLED=1`, `PLND_TYPE=1`, and the operator-selected
`PLND_EST_TYPE=0`. Do not change `PLND_STRICT`, descent rates, EKF sources, or
camera offsets during this procedure. The software already applies the stated
camera translation, so do not duplicate that compensation in parameters.

## Required log fields

Record these fields for every emitted target:

- monotonic message timestamp
- `angle_x` and `angle_y` in radians and degrees
- altitude in metres and its source, for example `rangefinder`
- actual output frequency
- detector confidence and bright area
- forward/right body offset
- horizontal error `sqrt(forward^2 + right^2)`
- flight mode, optical acquired/blocked state, and loss count

For touchdown, measure final horizontal error from the aircraft body origin to
the centre of the infrared board. Keep the raw camera recording, MAVLink log,
and controller diagnostics together.

## Abort conditions

Abort immediately and return to RC control if any condition occurs:

- unexpected mode change or any GUIDED velocity while in LAND/QLAND
- any `LANDING_TARGET` while in GUIDED, LOITER, or another non-landing mode
- stale/invalid altitude, unknown altitude source, or altitude discontinuity
- target loss/blocked state during a stage where continued correction is needed
- correction sign is opposite to the physical target direction
- oscillation grows for two consecutive corrections
- horizontal error grows for two consecutive observations
- output rate drops below `5 Hz` for more than one second while target is visible
- EKF, attitude, RC, serial/MAVLink, battery, or pre-arm warning
- vehicle exceeds the tether envelope or pilot cannot immediately take control

Target loss stops new `LANDING_TARGET` messages immediately. Acquire/loss
hysteresis affects lock state only; old measurements are never replayed.

## Stage 1: Propellers removed

1. Remove propellers and restrain the aircraft.
2. Start camera, detector, MAVLink logging, and controller diagnostics.
3. Keep the aircraft in STABILIZE or LOITER. Move the target through the image.
   Confirm no `LANDING_TARGET` and no GUIDED velocity are emitted.
4. Select GUIDED. Confirm the shared detector can acquire the board and only
   bounded horizontal GUIDED corrections are produced.
5. Select LAND, then QLAND if supported. Confirm only `LANDING_TARGET` is sent.
6. Place the target right of the aircraft. Confirm `angle_x > 0`.
7. Place the target forward of the aircraft. Confirm `angle_y < 0`.
8. Centre the aircraft body origin over the target. Confirm the software's
   `+0.04 m/+0.01 m` camera-offset compensation drives body error toward zero.
9. Cover the target for one frame and then continuously. Confirm transmission
   stops on the first missing/stale frame and reacquisition requires the
   configured consecutive detections.
10. Feed a frozen frame older than `0.20 s`. Confirm no target is emitted.

Proceed only if every sign, mode gate, stale-frame gate, and RC takeover check
passes.

## Stage 2: Tethered hover

Use an open test area, propeller guards where practical, two operators, and a
tether that cannot enter the propellers.

1. Hover manually at `2.0 m` above the target.
2. Verify valid horizontal position/attitude and a stable named altitude source.
3. Enter LAND briefly while the pilot remains ready to abort.
4. Confirm the aircraft moves toward the target without growing oscillation.
5. Repeat with the target offset forward, aft, left, and right.
6. Repeat one deliberate target-cover test and verify immediate message stop and
   predictable ArduPilot/pilot takeover behavior.

Do not proceed if the controller sign, altitude, output frequency, or loss
behavior differs from Stage 1.

## Stage 3: Height-by-height descent

Run separate flights rather than one uninterrupted first attempt:

1. `3.0 m` to `2.0 m`, then abort to hover.
2. `2.0 m` to `1.0 m`, then abort to hover.
3. `1.0 m` to `0.5 m`, then abort to hover.
4. `0.5 m` to approximately `0.2 m`, then abort before contact.

At each stop, compare horizontal error, confidence, output frequency, altitude
source, and signs. The error should converge or remain bounded. Investigate any
late-stage bias before attempting touchdown; do not compensate by changing
multiple parameters at once.

## Stage 4: Final touchdown

1. Centre over the board at a tested starting height.
2. Confirm target acquired, output near `10 Hz`, valid altitude, and no warnings.
3. Enter LAND and keep hands on the RC abort control.
4. Allow touchdown only while horizontal error remains convergent and all abort
   conditions remain false.
5. Disarm, then measure and record final forward/right and radial touchdown
   error.
6. Review the last five seconds of angle, height source, frequency, confidence,
   and error before repeating.

No test result is considered successful without both the MAVLink log and the
measured final touchdown error.
