# Slave aircraft RDK deployment

## Fixed topology

| Node | Address | Receive port | Peer |
| --- | --- | --- | --- |
| Rover coordinator | `192.168.4.2` | UDP `14610` | `192.168.4.3:14620` |
| Slave aircraft RDK | `192.168.4.3` | UDP `14620` | `192.168.4.2:14610` |

The ELF aircraft remains the main aircraft and keeps its existing ports. The
slave uses typed command, status, ACK and NACK datagrams; raw MAVLink is never
forwarded across this link. `OFFLINE` means no fresh slave status for three
seconds. `BLOCKED` is a separate reserved optical-gate state and is disabled
for the first slave deployment.

## Slave environment

Create `/etc/low-altitude-iot/slave.env` as root with mode `0600`:

```bash
SLAVE_NODE_ID=aircraft_2
SLAVE_LOCAL_PORT=14620
SLAVE_ROVER_IP=192.168.4.2
SLAVE_ROVER_PORT=14610
SLAVE_FC_DEVICE=/dev/serial/by-id/REPLACE_WITH_REAL_FLIGHT_CONTROLLER_ID
SLAVE_FC_BAUD=115200
SLAVE_OPTICAL_GATE_ENABLED=0
```

Find the real device with `ls -l /dev/serial/by-id/`. Do not replace it with
`ttyACM0` or `ttyUSB0`.

## Install and health

```bash
cd /path/to/Low-altitude-IoT-system
sudo bash ops/install_slave.sh
sudo systemctl status low-altitude-slave.service --no-pager
sudo bash ops/health_slave.sh
```

The installer backs up the prior release, validates a Copter heartbeat and
serial ownership, then restarts only the slave service. It never reboots the
board.

## Network profiles

Give `woshinailong` a static `192.168.4.3/24`, no default route, and higher
autoconnect priority than the phone profile. Keep the phone profile saved for
SSH recovery. The rover remains `192.168.4.2` and L610 remains its cloud
default route.

## Rollback

The verified single-aircraft baseline is tag
`national-finals-single-aircraft-baseline-20260819`. To disable the slave
without changing the rover or ELF:

```bash
sudo systemctl disable --now low-altitude-slave.service
```

Restore `/opt/low-altitude-iot/previous` and the timestamped files under
`/var/backups/low-altitude-iot/slave-*` only when a rollback is required.
