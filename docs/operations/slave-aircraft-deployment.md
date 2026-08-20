# Slave aircraft RDK deployment

## Fixed topology

| Node | Address | Receive port | Peer |
| --- | --- | --- | --- |
| Rover coordinator | `192.168.4.2` | UDP `14610` | `192.168.4.3:14620` |
| Slave aircraft RDK | `192.168.4.3` | UDP `14620` | `192.168.4.2:14610` |
| Follow target relay | `192.168.4.3` | UDP `14630` | leader system ID `1` |

The ELF aircraft remains the main aircraft and keeps its existing ports. The
slave uses typed command, status, ACK and NACK datagrams; raw MAVLink is never
forwarded across this link. `OFFLINE` means no fresh slave status for three
seconds. `BLOCKED` is a separate reserved optical-gate state and is disabled
for the first slave deployment.

Tuya product DP108 must be configured as:

```text
Name: 从机完整状态
Identifier: slave_state
Type: property
Data type: String
Maximum length: 1024
Transfer: read/write (rw)
```

## Slave environment

Create `/etc/low-altitude-iot/slave.env` as root with mode `0600`:

```bash
SLAVE_NODE_ID=aircraft_2
SLAVE_LOCAL_PORT=14620
SLAVE_ROVER_IP=192.168.4.2
SLAVE_ROVER_PORT=14610
SLAVE_FOLLOW_PORT=14630
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

## Safe indoor mission verification

Mission upload and flight-controller readback do not require arming or
selecting `AUTO`. Put both RDKs on `woshinailong`, upload a short aircraft-2
mission, and wait for the same mission ID to report `VERIFIED`. Indoor warnings
about GPS fix, satellites, Home, EKF or stale navigation data are expected:
they prevent execution but do not invalidate a successful upload/readback.

Only test execution outdoors after GPS, Home and EKF are valid. Arm under
supervision and select `AUTO` only after the ground station shows the intended
mission ID as verified.

## Follow mode

The main ELF publishes standard MAVLink2 `FOLLOW_TARGET` messages from the
main aircraft flight controller. The Rover RDK relays only complete message
ID 144 frames from system ID 1 to `192.168.4.3:14630`. The slave receiver
accepts only that fixed Rover address and injects the validated frame through
the slave service's existing, exclusive MAVLink session.

Apply the follower parameters only while the slave is disarmed and its service
is stopped so no second process owns the flight-controller serial device:

```bash
sudo systemctl stop low-altitude-slave.service
sudo /usr/local/lib/low-altitude-iot/configure_follow.py \
  --connection /dev/serial/by-id/REPLACE_WITH_REAL_FLIGHT_CONTROLLER_ID \
  --baud 115200 --apply
sudo systemctl start low-altitude-slave.service
sudo /usr/local/lib/low-altitude-iot/health_slave.sh
```

The tool snapshots every modified parameter before writing and prints the
snapshot and restore-script paths. The intended configuration is:

```text
SYSID_THISMAV=2
FOLL_ENABLE=1
FOLL_SYSID=1
FOLL_DIST_MAX=30
FOLL_OFS_TYPE=1
FOLL_OFS_X=0
FOLL_OFS_Y=-5
FOLL_OFS_Z=0
FOLL_ALT_TYPE=1
```

This places the follower 5 m to the leader's left, relative to leader heading,
at the same physical elevation. The ground-station `FOLLOW` button is shown
only for aircraft 2. It never arms the aircraft and must be used only outdoors
after both aircraft show fresh GPS, Home and EKF state. If the leader state is
stale or its coordinates are `0,0`, no `FOLLOW_TARGET` frame is published.

To restore the exact pre-change values, keep the slave disarmed and service
stopped, then run the timestamped restore script printed by the configuration
tool. Start the slave service and verify health afterward.

## Network profiles

Give `woshinailong` a static `192.168.4.3/24`, no default route, and higher
autoconnect priority than the phone profile. Keep the phone profile saved for
SSH recovery. The rover remains `192.168.4.2` and L610 remains its cloud
default route.

For maintenance, Tuya can send the hidden command envelope with target
`aircraft_2` and action `network_phone`. The slave writes a root-watched
request and switches only its Wi-Fi profile to `Mr.think的Mate 70 Pro+`; this
does not change a flight mode. Return to `woshinailong` from SSH with the saved
NetworkManager profile before an end-to-end test.

## Rollback

The verified single-aircraft baseline is tag
`national-finals-single-aircraft-baseline-20260819`. To disable the slave
without changing the rover or ELF:

```bash
sudo systemctl disable --now low-altitude-slave.service
```

Restore `/opt/low-altitude-iot/previous` and the timestamped files under
`/var/backups/low-altitude-iot/slave-*` only when a rollback is required.
