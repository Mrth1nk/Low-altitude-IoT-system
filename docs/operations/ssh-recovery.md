# RDK Wi-Fi modes and SSH recovery

The normal development state keeps `wlan0` on the operator's phone hotspot.
The aircraft SSID `woshinailong` is an explicit final-demo switch. Its profile
never owns the default route; Tuya traffic remains on the L610 ECM interface.

## Protected credentials

Create `/etc/low-altitude-iot/rdk-network.env` locally on the RDK:

```bash
sudo install -d -m 0700 /etc/low-altitude-iot
sudo install -m 0600 /dev/null /etc/low-altitude-iot/rdk-network.env
sudoedit /etc/low-altitude-iot/rdk-network.env
```

Add `RDK_WIFI_PASSWORD=...` without committing, pasting into logs, or sharing
the file. It must stay owned by root and mode `0600`.

## Development and final-demo modes

Prepare the aircraft profile without switching away from the SSH hotspot:

```bash
sudo /usr/local/lib/low-altitude-iot/configure_rdk_network.sh --mode development
```

Switch for one demo session:

```bash
sudo --preserve-env=RDK_WIFI_PASSWORD \
  /usr/local/lib/low-altitude-iot/configure_rdk_network.sh --mode demo
```

To reconnect to `woshinailong` after every reboot, install the protected
environment file and enable the optional unit:

```bash
sudo systemctl enable --now low-altitude-rdk-aircraft-network.service
```

The optional unit is not required by `low-altitude-rdk.service`. A missing
aircraft link therefore cannot stop Rover control or Tuya/L610 service.

## Restore phone-hotspot SSH

From a local console, replace the profile name below with the saved phone
hotspot connection:

```bash
sudo systemctl disable --now low-altitude-rdk-aircraft-network.service
sudo /usr/local/lib/low-altitude-iot/configure_rdk_network.sh \
  --mode recover --development-profile "Mr.think的Mate 70 Pro+"
```

Recovery only changes Wi-Fi connections. It does not bring down, reconfigure,
or replace routes on the L610 ECM interface.

Verify route ownership:

```bash
ip route get 139.196.6.123
ip route get 192.168.4.1
sudo /usr/local/lib/low-altitude-iot/health_rdk.sh
```

The first route must use the current L610 ECM interface (normally
`enxf04bb3b9ebe5`); the second must use `wlan0`.
