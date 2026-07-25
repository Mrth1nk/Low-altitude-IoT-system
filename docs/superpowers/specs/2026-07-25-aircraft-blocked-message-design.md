# Aircraft Blocked Message Design

## Scope

Change only the local Tuya cloud ground-station frontend. Do not modify RDK,
aircraft, MAVLink, mission, or Tuya transport behavior.

## Behavior

- Render an optical-link interruption in the existing aircraft message list.
- Use the same row layout as HEARTBEAT messages: timestamp, type, and text.
- Display `OPTICAL` as the type and `BLOCKED` as the text.
- Prefer the aircraft message timestamp; fall back to the cloud state receipt
  timestamp only when no message timestamp is available.
- Preserve prior aircraft messages while blocked and continue appending
  heartbeat messages after the optical link recovers.
- Deduplicate repeated cloud polling of the same blocked event by its timestamp
  and text, while retaining blocked events that have different timestamps.

## Verification

Add frontend-core tests for timestamp selection, blocked-message normalization,
deduplication, and history preservation. Run the complete ground-station Node
test suite.
