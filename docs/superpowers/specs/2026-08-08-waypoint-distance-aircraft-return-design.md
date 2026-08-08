# Waypoint Distance And Aircraft Return Design

## Goal

Improve mission planning without changing the proven Tuya, RDK, aircraft-link,
or MAVLink mission-upload path:

- show the distance of every route segment while selecting waypoints;
- append the aircraft's latest valid position as the final return waypoint when
  the operator uploads an aircraft mission;
- make the Rover's third mission input visibly identify its per-waypoint speed.

## Route Distance Behavior

Each queued waypoint shows its incoming segment distance in metres.

- Waypoint 1 uses the selected vehicle's latest valid current position as the
  segment origin.
- Every later waypoint uses the preceding queued waypoint as its origin.
- The distance is shown in the queue row and in the map-marker tooltip.
- Route distance is display-only and does not change any MAVLink mission item.
- If the selected vehicle has no valid current position, waypoint 1 displays
  that its distance is unavailable; later segments are still calculated.

Distances use the existing great-circle `distanceMeters` calculation.

## Aircraft Return Waypoint

When the operator presses **Upload aircraft mission**, the browser takes a
snapshot of the latest normalized aircraft position. The upload is rejected if
the state is stale, the optical link is blocked, or the position is invalid or
`0,0`.

Before building the mission command, the browser removes any previously
auto-generated return point and appends one new return point containing:

- the aircraft latitude and longitude at upload time;
- the same unified relative altitude selected for all aircraft waypoints;
- an internal `autoReturn` marker used only by the browser UI.

The generated return point becomes the final numbered map marker and queue row.
It is included in the existing `aircraft_mission` payload and therefore uses
the already verified Tuya-to-RDK-to-ELF-to-flight-controller handshake. The
ground station does not automatically switch the aircraft to `AUTO`.

Repeated uploads replace the previous generated return point instead of
accumulating duplicates. Clearing the aircraft queue removes it. Adding a new
manual aircraft waypoint after an upload removes the old generated return point
before adding the manual point, preserving the invariant that a return point is
always last.

## Rover Speed Field

The third Rover mission input remains the per-waypoint target speed in metres
per second. Its visible label/unit will be made explicit. Existing validation
and the generated `MAV_CMD_DO_CHANGE_SPEED` mission items remain unchanged.

## Scope

Only the local ground-station UI and its browser-side tests change. No RDK,
aircraft-board, Tuya product-definition, flight-controller parameter, or
mission-handshake code changes are required.

## Error Handling

- Aircraft mission upload is blocked with a clear message when current aircraft
  position cannot safely be captured.
- Invalid altitude or coordinates continue to use existing mission validation.
- Distance rendering never blocks editing; unavailable origins show a neutral
  placeholder.

## Testing

Automated tests will cover:

- first-segment distance from the current vehicle position;
- later segment distances from the preceding waypoint;
- a single aircraft return point appended at upload time;
- replacement rather than duplication on repeated uploads;
- rejection of stale, blocked, invalid, and `0,0` aircraft positions;
- preservation of Rover speed mission-item generation;
- unchanged mission command structure apart from the appended aircraft point.
