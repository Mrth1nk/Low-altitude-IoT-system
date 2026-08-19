# Competition demo runbook

## Safety boundary

`demo_preflight.sh` is read-only. It does not switch Wi-Fi, start services,
arm a vehicle, upload a mission, change a flight mode, or operate motors.
Run physical checks and powered tests under the team's separate safety
procedure.

Do not enable shell tracing and do not print or paste the Wi-Fi or LIOT
secrets. They remain in their root-owned environment files.

## Software rehearsal

From the repository:

```bash
python3 -m unittest discover -s tests/integration -v
ops/demo_preflight.sh --dry-run
```

Expected result: all integration tests pass and dry-run lists checks without
showing credentials or executing host commands.

## RDK preflight

On the RDK, after selecting the intended development or final-demo network:

```bash
cd /opt/low-altitude-iot/current
sudo ops/demo_preflight.sh --role rdk
```

Required passes:

- `low-altitude-rdk.service` is active.
- The Tuya probe route uses `enxf04bb3b9ebe5` or the current L610 ECM device.
- `192.168.4.1` has an exact route through `wlan0`.
- The RDK state snapshot is readable.

Stop if the script returns nonzero. Do not compensate by changing routes
during a live demonstration.

## ELF preflight

On the ELF board:

```bash
cd /opt/low-altitude-iot/current
sudo ops/demo_preflight.sh --role elf
```

Required passes:

- `low-altitude-aircraft.service` and `low-altitude-vision.service` are active.
- `aircraft-health.json`, `optical-state.json`, and the durable `inbox.json`
  are readable.

Stop if the script returns nonzero. Inspect the existing service and health
logs; the preflight itself intentionally performs no recovery action.

## Demonstration sequence

1. Confirm both role-specific preflights return zero.
2. Confirm the ground station shows Rover/Tuya online.
3. Confirm the aircraft display shows `LINK_BLOCKED` while the optical path is blocked and does not show detailed telemetry.
4. Restore optical lock and confirm detailed aircraft status resumes.
5. Submit the planned rover mission and observe its verified state.
6. Submit the planned aircraft mission and wait for `MISSION_STAGED`, then `VERIFIED`, before selecting AUTO.
7. Abort the software demonstration if retry exhaustion, NACK, stale optical state, unsafe residual mission, or route failure appears.

This runbook records software readiness only. Outdoor navigation, takeoff,
landing, and motor behavior require a separate supervised field checklist.

## Dual-aircraft additions

Before the dual-aircraft rehearsal, also run on the slave RDK:

```bash
sudo /usr/local/lib/low-altitude-iot/health_slave.sh
```

Confirm the rover coordinator is `192.168.4.2:14610`, the slave is
`192.168.4.3:14620`, both use `woshinailong`, and the rover cloud default route
still uses L610. Verify main and slave commands independently, then stop slave
status transmission and confirm only the slave becomes `OFFLINE` after three
seconds. The initial slave optical gate is disabled; `OFFLINE` must never be
presented as `BLOCKED`.
