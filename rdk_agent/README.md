# RDK X5 + L610 + TuyaLink Rover

This project turns the RDK X5 into the rover-side controller for a competition demo.

- L610 uses ECM USB network mode, not PPP.
- RDK connects to TuyaLink over cellular.
- RDK connects to ArduPilot Rover through MAVLink on `/dev/ttyACM0`.
- A local web ground station runs on port `8080`.
- Tuya product functions currently use string fields for robust debug import:
  `rover_state`, `command`, `target_lat`, `target_lng`, `target_speed`, `steering`, `throttle`.

## Start

```bash
cd ~/uav_tuya_agent
./start_rover_stack.sh
```

Open:

```text
http://192.168.43.175:8080/
```

## Check

```bash
cd ~/uav_tuya_agent
. .venv/bin/activate
python -m unittest discover -s tests -v
python tuya_rover_agent.py check-l610 --at-port /dev/ttyUSB0
tail -f rover_agent.log
```

## Safety

The STOP command is always safe to send. Manual steering and throttle are limited to `-100..100`. The agent reports `fc_link=false` if the flight controller is not detected.
