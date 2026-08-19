# Dual-UAV Follow Design

## Goal

Extend the verified dual-aircraft system so aircraft 2 can follow aircraft 1
at a five-meter left offset relative to aircraft 1 heading. Preserve the
existing Tuya, rover, aircraft command, mission, infrared tracking and
precision-landing behavior.

The handoff document supplied by the operator is a technical reference. This
design adapts it to the deployed single-owner MAVLink architecture instead of
starting independent processes that compete for flight-controller serial
ports.

## Safety Boundary

- Never arm, spin motors, enter Follow mode or perform a live flight during
  automated deployment.
- Snapshot every affected ArduPilot parameter before writing it.
- Re-read every written parameter and retain a restore script.
- Require distinct system IDs: leader `1`, follower `2`.
- Stop publishing stale leader targets; do not automatically change follower
  mode when the target stream expires.
- Keep all existing flight-controller serial links single-owner.

## Architecture

### Follow Target Data Path

1. The ELF aircraft agent remains the only reader of the leader USB MAVLink
   link.
2. A bounded follow-target publisher observes leader heartbeat,
   `GLOBAL_POSITION_INT` and attitude through that existing session.
3. When the leader position is valid and fresh, it produces standard MAVLink2
   `FOLLOW_TARGET` frames at approximately 10 Hz with source system ID `1`.
4. The existing aircraft telemetry transport carries the frame to the rover
   RDK. The rover validates message ID, source ID and freshness, then relays it
   over the local `woshinailong` network to aircraft 2.
5. Aircraft 2 receives only validated `FOLLOW_TARGET` frames and writes them
   through its existing `MavlinkSession`, which remains the sole owner of the
   follower flight-controller serial device.

The direct ELF-to-follower Wi-Fi fan-out described in the reference handoff is
not assumed because multi-client serial-module downlink behavior has not yet
been proven. The rover relay uses the already verified local path and keeps
cloud aggregation centralized.

### Command Path

The ground station gains a `FOLLOW` command for aircraft 2. It follows the
existing route:

```text
ground station -> Tuya -> L610 -> rover RDK -> aircraft 2 RDK -> follower FC
```

The command is allowed only while aircraft 2 state is fresh and its local link
is online. The automated deployment does not invoke this command.

### Maintenance Network Command

Add a hidden `aircraft_2_network_phone` cloud command. The rover forwards a
typed system action to aircraft 2. Aircraft 2 persists and acknowledges the
request before a root-owned systemd path service activates the configured
phone hotspot. This command is intentionally not shown as a ground-station
button.

After aircraft 2 leaves `woshinailong`, cloud control cannot switch it back;
the operator restores `woshinailong` over SSH.

## Follower Parameters

After a parameter snapshot and capability check, configure and verify:

```text
SYSID_THISMAV = 2
FOLL_ENABLE   = 1
FOLL_SYSID    = 1
FOLL_DIST_MAX = 30
FOLL_OFS_TYPE = 1
FOLL_OFS_X    = 0
FOLL_OFS_Y    = -5
FOLL_OFS_Z    = 0
FOLL_ALT_TYPE = 1
```

The aircraft take off from the same physical elevation, so relative-to-home
altitude is appropriate. Parameters unrelated to Follow remain unchanged.

## Stale-Target Behavior

- The publisher requires fresh leader heartbeat and position.
- If leader position is older than 1.5 seconds, publishing stops.
- The relay rejects malformed frames, wrong source IDs and stale frames.
- The follower bridge does not request `LOITER`; ArduPilot handles loss of the
  Follow target as requested by the operator.
- Link-loss state and target age are reported for diagnostics.

## Ground Station Changes

- Add `FOLLOW` beside the aircraft 2 mode controls.
- Remove the five transaction lamps (`upload`, `verified`, `ready`, `AUTO`,
  `reached`) from rover, aircraft 1 and aircraft 2 panels.
- Keep task results in the task field, message list and operation log.
- Display aircraft 2 coordinates as `0.00000, 0.00000` indoors when no valid
  GPS position exists, while retaining `position_observed=false` for mission
  and map safety gates.
- Display valid coordinates with the same five decimal places as aircraft 1.

## Verification

### Automated

- Unit tests for Follow message construction, source ID, stale suppression,
  frame validation and serial single-owner injection.
- Integration test for ELF -> rover -> aircraft 2 target relay.
- Tests for the aircraft 2 maintenance-network command and ACK-before-switch
  ordering.
- Ground-station tests for the Follow button, removed transaction row and
  zero-coordinate fallback.
- Existing Python and Node suites must remain green.

### Hardware, No Propellers

1. Confirm leader and follower serial owners are unchanged and unique.
2. Confirm leader system ID `1` and follower system ID `2`.
3. Confirm fresh `FOLLOW_TARGET` messages arrive at the follower FC at the
   expected rate with source system ID `1`.
4. Stop the leader position source and confirm target frames cease within
   1.5 seconds without an automatic mode command.
5. Read back every Follow parameter.
6. Confirm no ARM or Follow-mode command was issued by deployment.

Outdoor arming, mode selection and flight acceptance remain manual operator
steps.

## Recovery

- Restore the saved follower parameter file through the generated script.
- Restore the previous application release using the existing atomic rollback
  process.
- Restore the prior ground-station commit or the verified dual-aircraft tag.
- The existing single-aircraft and dual-aircraft recovery tags remain intact.
