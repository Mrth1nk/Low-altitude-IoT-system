# Rover Mission Quality Design

## Scope

Harden Task 4 without changing the ground-station trigger or flight-controller
parameters. Rover missions run asynchronously so the Tuya reporting loop,
aircraft transport pump, and manual-control timeout remain responsive.

## Ownership And Concurrency

`RoverMavlink` remains the sole owner of the flight-controller connection.
Every read or write uses one re-entrant session lock. `RoverMissionWorker`
serializes mission jobs on one background thread. The main loop only validates
and queues a mission, then polls immutable results by `command_id`.

Manual commands may acquire the same session lock between mission protocol
operations. A mission transaction never has two readers. Disconnect increments
a connection generation and invalidates mission verification and all navigation
freshness.

## Transaction Bounds

Each mission has one monotonic operation deadline and a per-sequence request
limit. Valid repeated requests do not reset either budget. Missions exceeding
100 executable items are rejected before `MISSION_CLEAR_ALL`.

## Failure Safety

Any upload, download, or verification failure marks the mission unverified and
not executable. If a mission protocol transaction is active, the worker sends
`MISSION_ACK` with `MAV_MISSION_OPERATION_CANCELLED`, then sends
`MISSION_CLEAR_ALL` and waits for its acknowledgement. The previous mission is
not restored; clearing partial or unverified state is the explicit safety rule.

## Verification And Execution Gate

Readback compares count, command, frame, integer coordinates, altitude,
`param1` through `param4` with tolerance, and `autocontinue` exactly.

GPS, global position, EKF, and Home each carry a monotonic timestamp and
connection generation. AUTO requires every source to belong to the current
connection and be no older than the configured freshness interval, defaulting
to three seconds.

## Runtime Results

Jobs expose `queued`, `uploading`, `verifying`, `verified`, or `failed` with
their `command_id`. `MissionError` becomes a typed failed receipt and does not
escape the main loop. Tests use fake clocks and transports, plus an actual RDK
Python 3.10 `/tmp` test copy without restarting services.
