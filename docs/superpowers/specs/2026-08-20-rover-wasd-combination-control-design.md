# Rover WASD Combination Control Design

## Scope

Only change the local Tuya cloud ground-station frontend keyboard handling for
the rover. Do not change rover firmware, RDK command processing, aircraft or
slave-aircraft behavior.

## Behavior

- Track held `W`, `A`, `S`, and `D` keys as a set.
- `W/S` produce forward/reverse throttle and cancel each other when both held.
- `A/D` produce left/right steering and cancel each other when both held.
- Send the combined steering/throttle command immediately when the held set
  changes, then refresh it every 250 ms while at least one effective axis is
  active.
- Releasing one key immediately updates control from the remaining held keys.
- Releasing all keys stops command refreshes. It does not send `stop`, a zero
  throttle command, or any other release command.
- Ignore keyboard control while typing in inputs or text areas.
- Preserve the existing pointer controls and arrow-key behavior.

## Safety

The existing RDK manual-command timeout remains the final neutralization
fallback after keyboard messages stop. No backend timeout or vehicle parameter
is changed.

## Testing

Add unit coverage for axis composition, opposite-key cancellation, case
normalization, and unrelated keys. Run the complete ground-station Node test
suite after the frontend integration.
