# Task 13 software integration results

Date: 2026-07-24

## Scope

These results are from deterministic software tests. No aircraft, rover,
propeller, wheel, outdoor GPS, radio-range, or live motor test was performed.
No real credential is stored or printed.

## Command

```bash
python3 -m unittest discover -s tests/integration -v
bash -n ops/demo_preflight.sh
```

## Result

Six integration tests passed:

| Scenario | Verified behavior |
| --- | --- |
| Packet loss | Missing mission item is delivered on the bounded retry deadline. |
| Duplicate | A repeated item is idempotent and does not duplicate the durable mission. |
| Reordering | Mission items may arrive out of order and are assembled by index. |
| Bad CRC | An authenticated envelope containing a corrupt LIOT frame is rejected before staging. |
| Delay | Nothing is retransmitted before the retry deadline; pending frames resume afterward. |
| Durable staging | Restarting `DurableInbox` restores exactly one complete `MISSION_STAGED` record. |
| End to end | Wrapped ground payload is normalized, routed by RDK, encoded as LIOT, staged by ELF, uploaded to a fake FC, read back, compared, and recorded as `VERIFIED`. |
| Optical loss | Only `LINK_BLOCKED` reason/timestamp is emitted; telemetry detail is absent and commands do not enter the inbox. |
| Preflight | Dry-run is secret-safe/read-only; role checks fail closed for an invalid L610 route. |

The fake FC uses the ArduPilot mission sequence exercised by the production
worker: clear ACK, count, out-of-order item requests, upload ACK, mission
download, item readback comparison, final ACK, then AUTO only when the
navigation gate is ready.

## Limits

Passing these tests establishes software protocol behavior, persistence, and
gating. It does not establish real serial integrity, optical alignment,
airworthiness, GPS reception, actuator direction, or field radio performance.
