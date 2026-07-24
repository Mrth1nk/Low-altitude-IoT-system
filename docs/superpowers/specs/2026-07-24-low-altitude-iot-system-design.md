# Low-altitude IoT System Design

## Objective

Build a reliable competition demonstration system in which a Tuya-cloud ground
station controls an ArduPilot Rover through an RDK X5 and controls an ArduPilot
aircraft through an RDK-to-ELF serial Wi-Fi telemetry link. The aircraft tracks
an infrared beacon in GUIDED, flies operator-defined AUTO missions, reacquires
the beacon after the operator returns it to GUIDED, and performs precision
landing in LAND.

The existing RDK-to-Tuya path through the Fibocom L610 is already stable and
must be preserved. The principal refactor targets are the RDK-to-ELF command
path, aircraft mission upload, serial ownership, observability, and the
infrared tracking and precision-landing control states.

## Repository Structure

- `ground-station/`: local web application that reads and writes Tuya Cloud.
- `rdk-agent/`: RDK X5 Tuya agent, Rover MAVLink executor, aircraft link, and
  boot/network services.
- `aircraft-agent/`: ELF aircraft message receiver, durable command queue,
  flight-controller transaction worker, status collector, and service files.
- `vision/`: infrared detector, GUIDED tracking controller, LAND precision
  landing controller, camera geometry, and optical-link state publisher.
- `shared-protocol/`: versioned RDK-to-ELF frame schema and reference codecs.
- `tests/`: protocol, mission, visual-control, integration, and replay tests.
- `ops/`: deployment, health-check, parameter backup/restore, and demonstration
  preflight scripts.
- `docs/`: architecture, operations, test evidence, and recovery procedures.

## System Boundaries

The local ground station communicates only with Tuya Cloud. It never depends on
an RDK HTTP address during the demonstration.

The RDK owns:

- L610/TuyaLink cloud connectivity.
- The USB MAVLink connection to the Rover flight controller.
- Rover command execution and Rover mission upload.
- Routing aircraft commands into the reliable RDK-to-ELF protocol.
- Combining Rover and permitted aircraft state into the Tuya state report.

The ELF owns:

- The serial Wi-Fi telemetry endpoint.
- Durable receipt and ordering of aircraft commands.
- Aircraft flight-controller command and mission transactions over USB.
- Aircraft status collection.
- Infrared camera processing and optical-link state.
- GUIDED visual corrections and LAND precision-landing targets over a separate
  flight-controller UART.

## Network Topology

The RDK connects `wlan0` to SSID `woshinailong`; the Wi-Fi password is supplied
at deployment and is never stored in Git. This network is local-only and must
have `ipv4.never-default yes`. The L610 ECM interface remains the default route
for Tuya traffic.

The RDK boot service waits for both the L610 route and the local aircraft link.
Failure of the aircraft link must not prevent Rover/Tuya operation. Failure of
the L610 route must be visible in health state and must not silently move cloud
traffic to Wi-Fi.

## Reliable RDK-to-ELF Protocol

Every command has a UUID `command_id`, protocol version, monotonic frame
sequence, payload type, payload length, and checksum. Commands are idempotent:
repeated frames are acknowledged but never executed twice.

Small commands use a single framed message. Aircraft missions use:

1. `MISSION_BEGIN`: mission ID, total item count, and mission checksum.
2. `MISSION_ITEM`: indexed waypoint with latitude, longitude, altitude, command,
   frame, and acceptance radius.
3. `MISSION_COMMIT`: requests validation and durable staging.
4. `MISSION_STAGED`: confirms that ELF has every item and the checksum matches.
5. Mission execution status events: `FC_UPLOAD_STARTED`, `FC_REQUEST`,
   `FC_ACK`, `FC_VERIFY_STARTED`, `VERIFIED`, or a typed failure.

ELF stores a committed mission before talking to the aircraft flight
controller. Missing frames are requested by index. Reconnection resumes the
same transaction. A command remains traceable by the same ID from the browser
through Tuya, RDK, ELF, and the flight-controller transaction log.

## Rover Mission Transaction

Rover missions are validated and uploaded directly by the RDK using the
standard MAVLink mission protocol:

1. Wait for a Rover heartbeat and identify target system/component.
2. Send `MISSION_CLEAR_ALL` and wait for acknowledgement.
3. Send `MISSION_COUNT`.
4. Respond to each `MISSION_REQUEST_INT` with the requested
   `MISSION_ITEM_INT`.
5. Require an accepted `MISSION_ACK`.
6. Download the mission and compare item count, coordinates, and commands.
7. Mark the mission `VERIFIED`.

The ground station can request AUTO only after verification. A valid GPS fix,
Home, and healthy EKF are required for execution. Indoor testing may verify
mission write/readback but must not claim that the mission is safe to execute.

## Aircraft Mission Transaction

Only the ELF mission worker initiates an aircraft mission transaction. It
performs the same standard MAVLink handshake over the flight-controller USB
connection after the complete mission has been staged locally.

Mission protocol replies are consumed by the transaction that owns them rather
than by an unrelated telemetry reader. The transaction has explicit deadlines,
bounded retries, sequence checks, and typed failure results. After an accepted
`MISSION_ACK`, ELF downloads the mission and verifies item count, coordinates,
altitudes, frames, and commands.

The ground station enables AUTO only after a `VERIFIED` result. The software
does not append, move, or synthesize a final aircraft waypoint. The operator
defines the complete aircraft route and may place the final point near the
Rover.

## Serial Ownership

Device binding is explicit and stable:

- Aircraft flight-controller USB: `/dev/ttyACM0`, owned by the aircraft agent.
- Serial Wi-Fi telemetry: `/dev/ttyUSB0`, owned by the RDK-to-ELF link.
- Visual-control MAVLink UART: `/dev/ttyS9`, owned by the vision controller.

Production services never guess serial roles from enumeration order. Deployment
checks device identity and refuses to start on ambiguous mappings.

Aircraft mission upload and visual tracking may run concurrently because they
use separate flight-controller channels. The mission worker serializes all
transactions on `/dev/ttyACM0`; the vision process is the sole writer to
`/dev/ttyS9`.

## Vision State Machine

The camera orientation is `FORWARD`. Camera displacement is configured as
4 cm toward the nose and approximately 1 cm to the right.

The detector outputs normalized target position, angular offset, confidence,
blob area, timestamp, and frame sequence. It uses bounded thresholds, temporal
filtering, and separate acquire/loss counts to prevent lock-state flicker.

Modes:

- `GUIDED`: send bounded horizontal velocity corrections. Corrections taper
  near the target and stop inside a configurable deadband.
- `LAND` and `QLAND`: send ArduPilot `LANDING_TARGET` messages only.
- `AUTO`, `LOITER`, `STABILIZE`, and other modes: detect and log, but send no
  visual position correction.

When the aircraft changes from AUTO back to GUIDED, tracking resumes only after
the beacon is reacquired. The vision application does not change flight mode
when the beacon is lost; existing flight-controller behavior and the RC
operator remain authoritative.

## Optical-Link Simulation

Optical communication is modeled strictly:

- `LOCKED`: aircraft commands may pass from RDK to ELF, and permitted aircraft
  telemetry is returned at 1 Hz.
- `BLOCKED`: all cloud-originated aircraft commands are rejected at the RDK and
  again at ELF. No detailed aircraft telemetry is returned. The only link
  message is `OPTICAL_LINK_BLOCKED` with protocol sequence and source timestamp.

No emergency cloud command bypasses this gate. When blocked, the aircraft is
controlled only by the RC transmitter and flight-controller-native behavior.
Hysteresis prevents a single noisy frame from repeatedly opening and closing
the link.

## Tuya State And Ground Station

The existing Tuya authentication, L610 path, and product property integration
are preserved behind stable interfaces.

The ground station shows Rover and aircraft panels side by side. The map can
edit either a Rover route or an aircraft route. Aircraft editing adds a shared
altitude control. It displays Rover position and track, both planned routes,
the current mission item, and transaction progress.

Command progress is explicit:

`cloud_received → rdk_validated → elf_staged → fc_uploaded → verified →
executing → completed`

Failures identify their boundary and reason. Aircraft messages use their source
receive time. Repeated heartbeats remain separate rows. When the optical link
is blocked, detailed aircraft fields are cleared and the panel displays
`OPTICAL LINK BLOCKED`.

The RDK reports one compact cloud state snapshot per second. High-rate camera
and MAVLink data remain in local structured logs.

## Error Handling And Recovery

- Every service exposes a health snapshot with link state, queue depth, current
  command ID, serial ownership, heartbeat age, and last typed error.
- Incomplete aircraft mission staging survives process restart.
- A flight-controller transaction has bounded retries and cannot overlap
  another transaction.
- Stale Tuya or wireless commands are rejected by timestamp and command ID.
- Mode and arm commands require acknowledgements where supported and report
  timeout separately from rejection.
- Network setup is reversible and includes an SSH recovery procedure.
- Any flight-controller parameter change requires official ArduPilot
  documentation, a captured original value, a reason, a test result, and a
  restore command.

## Verification Strategy

Testing proceeds in increasing-risk stages:

1. Unit tests for frame encoding, checksums, deduplication, mission state
   machines, coordinate conversion, camera geometry, and optical gating.
2. Fault-injection tests for dropped, duplicated, reordered, and delayed
   RDK-to-ELF frames.
3. Recorded MAVLink replay tests for both mission handshakes.
4. Two-board bench tests with structured logs at every boundary.
5. Flight-controller tests without propellers/wheels, including mission
   upload/readback without GPS.
6. Outdoor Rover mission execution with immediate RC takeover available.
7. Tethered low-altitude aircraft tracking and mode transition tests.
8. Incremental precision-landing tests with measured landing error.
9. Full demonstration rehearsal with a preflight health report and archived
   logs.

No test stage is described as flight-safe until the preceding stage has passed
and the operator has verified RC takeover.

## Acceptance Scenario

At boot, the RDK connects to `woshinailong` for the aircraft link while the L610
remains the Tuya default route. The cloud ground station displays Rover and
permitted aircraft state.

The operator uploads a Rover route. The RDK writes, reads back, and verifies it;
the Rover executes every waypoint and stops at the endpoint. In GUIDED, the
aircraft remains centered above the moving infrared beacon.

The operator uploads an independently defined aircraft route. RDK transfers it
completely to ELF; ELF writes and reads it back from the aircraft flight
controller; the aircraft executes it in AUTO. The operator places the final
waypoint near the Rover and switches back to GUIDED, where the aircraft
reacquires the beacon and tracks it.

In LAND, the aircraft uses precision landing to remain centered on the infrared
beacon through touchdown. Detailed aircraft communication is available only
while the beacon is locked; otherwise the ground station shows
`OPTICAL LINK BLOCKED` and RC control remains the only operator control path.
