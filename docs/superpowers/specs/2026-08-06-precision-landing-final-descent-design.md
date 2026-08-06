# Precision Landing Final-Descent Cutoff Design

## Goal

Preserve the currently proven GUIDED infrared tracking and precision-landing
behavior above 25 cm, then stop camera-derived horizontal corrections for the
last approximately 25 cm so a frame filled by the infrared panel cannot create
large false offsets immediately before touchdown.

## Baseline And Recovery

- Record the exact local vision sources that match the running ELF files in a
  dedicated Git checkpoint and tag.
- Save the running ELF vision package, service unit, environment file, and
  file hashes under a timestamped backup directory before deployment.
- Recovery restores that directory and restarts only
  `low-altitude-vision.service`; the board is not rebooted.

## Behavior

- Add `PLND_FINAL_DESCENT_ALT_M`, defaulting to `0.25` meters.
- Apply the cutoff only while the flight-controller mode is `LAND` or `QLAND`.
- Enter final-descent state only when altitude is positive, has a known source,
  and is at or below the configured threshold.
- Latch final-descent state until the aircraft leaves a landing mode. Altitude
  noise near 25 cm therefore cannot repeatedly enable and disable correction.
- While latched, continue camera capture, target detection, optical-link state,
  video preview, and telemetry publication, but emit no new
  `LANDING_TARGET` correction.
- Unknown or zero altitude never activates the cutoff. Existing behavior is
  retained when the threshold cannot be evaluated safely.
- GUIDED tracking is unchanged.

## Flight-Controller Interaction

ArduPilot documents `PLND_ALT_MIN` as the height below which loss of a landing
target continues as a vertical landing. Before flight, read and record
`PLND_STRICT`, `PLND_ALT_MIN`, and availability of `PLND_ALT_CUTOFF`. Do not
write a parameter unless the current value would cause a retry or hover after
the script intentionally stops target updates. Any parameter write must be
recorded with its original value and read back after the change.

The current ELF telemetry reports `relative_altitude`, not a rangefinder, so
`PLND_ALT_CUTOFF` alone is not used for this iteration.

## Diagnostics

- Publish whether final descent is active and the configured cutoff in the
  optical-state snapshot.
- Keep the last landing-control record unchanged but expose that transmission
  has been suppressed by the final-descent latch.
- The video overlay should display `FINAL DESCENT` while latched.

## Verification

- Unit tests cover activation at and below 25 cm, no activation above the
  threshold, no activation for zero/unknown altitude, latching against noisy
  altitude, reset after leaving LAND, and no effect on GUIDED tracking.
- A propeller-off test supplies simulated altitude values and verifies the
  `LANDING_TARGET` counter stops below the threshold while camera and optical
  state continue updating.
- Deployment restarts only the vision service and verifies service health,
  hashes, camera preview, and diagnostic state. Actual touchdown accuracy still
  requires a supervised flight test.
