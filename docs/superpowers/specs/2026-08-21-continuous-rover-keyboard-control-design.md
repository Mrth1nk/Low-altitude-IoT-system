# Continuous Rover Keyboard Control Design

## Goal

Holding WASD or an arrow key must produce continuous rover motion. Combined
keys update steering and throttle together. Releasing a key immediately applies
the remaining held-key vector; releasing every key sends one neutral manual
command (`steering=0`, `throttle=0`) without issuing `STOP`.

## Root Cause

The browser currently starts a 250 ms interval that launches overlapping Tuya
requests. Cloud latency can reorder those requests. Every delivered manual
command also asks the rover flight controller to enter MANUAL again before
refreshing RC override. This turns one held key into independent, delayed
commands and can let the RDK safety timeout neutralize the rover between them.

## Design

The browser will use one drive-command pump with at most one request in flight.
It stores the latest keyboard/pointer vector and coalesces intermediate states.
After a request completes, it sends the newest state immediately when it has
changed; otherwise it sends a bounded keepalive while a non-neutral state is
held. A transition to no held keys queues exactly one neutral manual command.

The RDK rover adapter will avoid redundant mode changes. It will enter MANUAL
only when the latest flight-controller heartbeat does not already report
MANUAL, then refresh RC override directly for subsequent keepalives. The
existing 1.8 second timeout remains the final failsafe and neutralizes control
if browser or cloud traffic disappears.

## Safety And Verification

- Opposite keys cancel on their own axis.
- At most one drive HTTP request is active.
- Late completion cannot overwrite a newer held-key state.
- Releasing all keys emits neutral manual control, not STOP.
- Browser focus loss and page hide also request neutral control.
- Unit tests cover request serialization, state coalescing, neutral release,
  and redundant MANUAL-mode suppression.
- Existing mission, aircraft, Tuya, and rover tests must remain green.
