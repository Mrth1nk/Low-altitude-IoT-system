from __future__ import annotations

import glob
import copy
import threading
import time

try:
    from .rover_state import RoverCommand, RoverTelemetry, clamp
    from .rover_mission import (
        MissionError,
        MissionItem,
        RoverMissionManager,
        RoverMissionWorker,
    )
except ImportError:
    from rover_state import RoverCommand, RoverTelemetry, clamp
    from rover_mission import (
        MissionError,
        MissionItem,
        RoverMissionManager,
        RoverMissionWorker,
    )

try:
    from pymavlink import mavutil
except Exception:
    mavutil = None


DEFAULT_URLS = ["/dev/ttyACM0", "/dev/ttyUSB0", "/dev/ttyS1", "/dev/ttyS2"]


def discover_mavlink_urls(configured: str | list[str] | None = None) -> list[str]:
    urls: list[str] = []
    if isinstance(configured, str) and configured:
        urls.extend([part.strip() for part in configured.split(",") if part.strip()])
    elif isinstance(configured, list):
        urls.extend([str(part) for part in configured if str(part)])
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/ttyS*"):
        urls.extend(glob.glob(pattern))
    urls.extend(DEFAULT_URLS)
    seen = set()
    ordered = []
    for url in urls:
        if url not in seen:
            ordered.append(url)
            seen.add(url)
    return ordered


class RoverMavlink:
    def __init__(self, urls: list[str], baud: int = 115200):
        self.urls = urls
        self.baud = baud
        self.conn = None
        self.url = ""
        self.target_system = 1
        self.target_component = 0
        self.last_heartbeat = 0.0
        self.last_command_time = 0.0
        self.rc_params_loaded = False
        self.steer_min = 1000
        self.steer_trim = 1500
        self.steer_max = 2000
        self.throttle_min = 1000
        self.throttle_trim = 1500
        self.throttle_max = 2000
        self.home_valid = False
        self.connection_generation = 0
        self.home_updated_monotonic = 0.0
        self.last_home_request = 0.0
        self.mission_manager = None
        self.session_lock = threading.RLock()
        self.mission_worker = RoverMissionWorker(self._execute_mission_job)

    def connect(self, timeout: float = 2.5) -> bool:
        with self.session_lock:
            return self._connect_locked(timeout)

    def _connect_locked(self, timeout: float = 2.5) -> bool:
        if mavutil is None:
            return False
        if self.conn and time.time() - self.last_heartbeat < 5:
            return True
        self.close()
        for url in self.urls:
            try:
                conn = mavutil.mavlink_connection(url, baud=self.baud, autoreconnect=True, source_system=255)
                hb = conn.wait_heartbeat(timeout=timeout)
                if hb is not None:
                    self.conn = conn
                    self.url = url
                    self.target_system = hb.get_srcSystem() or conn.target_system or 1
                    # pymavlink's conn.target_component is the local default
                    # (often 0), not the component that emitted the heartbeat.
                    # Rover mission services on this FC listen on component 1.
                    self.target_component = hb.get_srcComponent() or 1
                    self.last_heartbeat = time.time()
                    self.home_valid = False
                    self.home_updated_monotonic = 0.0
                    self.mission_manager = RoverMissionManager(
                        RoverMavlinkTransport(self),
                        vehicle_system=self.target_system,
                        vehicle_component=self.target_component,
                        clear_before_upload=False,
                    )
                    self.load_rc_params()
                    RoverMavlinkTransport(self).request_home_position()
                    return True
                conn.close()
            except Exception:
                continue
        return False

    def close(self) -> None:
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None
        self.connection_generation += 1
        self.home_valid = False
        self.home_updated_monotonic = 0.0
        self.mission_manager = None

    def update_telemetry(self, telemetry: RoverTelemetry) -> RoverTelemetry:
        if not self.session_lock.acquire(blocking=False):
            telemetry.mission_status = "mission transaction active"
            return telemetry
        try:
            return self._update_telemetry_locked(telemetry)
        finally:
            self.session_lock.release()

    def _update_telemetry_locked(self, telemetry: RoverTelemetry) -> RoverTelemetry:
        if not self.connect(timeout=0.2):
            telemetry.fc_link = False
            return telemetry
        telemetry.fc_link = True
        telemetry.connection_generation = self.connection_generation
        deadline = time.time() + 0.12
        while time.time() < deadline:
            try:
                msg = self.conn.recv_match(blocking=False)
            except Exception as exc:
                telemetry.fc_link = False
                telemetry.fault_text = f"mavlink receive error: {exc}"
                self.close()
                return telemetry
            if msg is None:
                time.sleep(0.02)
                continue
            typ = msg.get_type()
            if typ == "BAD_DATA":
                continue
            if typ == "HEARTBEAT":
                self.last_heartbeat = time.time()
                telemetry.armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                try:
                    telemetry.flight_mode = mavutil.mode_string_v10(msg).lower()
                except Exception:
                    telemetry.flight_mode = str(getattr(msg, "custom_mode", "unknown"))
            elif typ == "GLOBAL_POSITION_INT":
                telemetry.lat = msg.lat / 1e7
                telemetry.lng = msg.lon / 1e7
                telemetry.position_observed = True
                telemetry.altitude = msg.relative_alt / 1000.0
                telemetry.heading = int((msg.hdg or 0) / 100)
                telemetry.position_generation = self.connection_generation
                telemetry.position_updated_monotonic = time.monotonic()
            elif typ == "VFR_HUD":
                telemetry.ground_speed = float(msg.groundspeed)
                telemetry.heading = int(getattr(msg, "heading", telemetry.heading))
            elif typ == "SYS_STATUS":
                battery = getattr(msg, "battery_remaining", -1)
                if battery >= 0:
                    telemetry.battery_percent = int(battery)
            elif typ == "GPS_RAW_INT":
                telemetry.gps_fix_type = int(getattr(msg, "fix_type", 0) or 0)
                telemetry.satellites_visible = int(getattr(msg, "satellites_visible", 0) or 0)
                telemetry.gps_generation = self.connection_generation
                telemetry.gps_updated_monotonic = time.monotonic()
            elif typ == "EKF_STATUS_REPORT":
                telemetry.ekf_flags = int(getattr(msg, "flags", 0) or 0)
                telemetry.ekf_generation = self.connection_generation
                telemetry.ekf_updated_monotonic = time.monotonic()
            elif typ == "HOME_POSITION":
                self.home_valid = bool(
                    abs(int(getattr(msg, "latitude", 0) or 0)) > 0
                    and abs(int(getattr(msg, "longitude", 0) or 0)) > 0
                )
                self.home_updated_monotonic = time.monotonic()
                telemetry.home_generation = self.connection_generation
                telemetry.home_updated_monotonic = self.home_updated_monotonic
            elif typ in ("MISSION_CURRENT", "MISSION_ITEM_REACHED"):
                if self.mission_manager is not None:
                    self.mission_manager.observe(msg)
                    self.mission_manager.publish_status(telemetry)
            elif typ == "STATUSTEXT":
                if self.mission_manager is not None:
                    self.mission_manager.observe(msg)
                    self.mission_manager.publish_status(telemetry)
        return telemetry

    def apply(self, command: RoverCommand, telemetry: RoverTelemetry) -> tuple[bool, str]:
        if not self.session_lock.acquire(blocking=False):
            return False, "flight controller busy: mission transaction active"
        try:
            return self._apply_locked(command, telemetry)
        finally:
            self.session_lock.release()

    def _apply_locked(self, command: RoverCommand, telemetry: RoverTelemetry) -> tuple[bool, str]:
        telemetry.last_command = command.command
        telemetry.control_mode = command.control_mode
        telemetry.target_lat = command.target_lat
        telemetry.target_lng = command.target_lng
        telemetry.target_speed = command.target_speed
        telemetry.steering = command.steering
        telemetry.throttle = command.throttle
        if not self.connect(timeout=0.5):
            return False, "flight controller not connected"
        cmd = command.command
        try:
            if cmd in ("noop", ""):
                return True, "noop"
            if cmd in ("stop", "brake"):
                self.stop()
                return True, "stopped"
            if cmd == "arm":
                ok, detail = self.safe_arm()
                return ok, detail
            if cmd == "disarm":
                ok, detail = self.arm(False)
                return ok, detail
            if cmd in ("manual", "drive"):
                self.manual(command.steering, command.throttle)
                return True, "manual control sent"
            if cmd in ("guided", "auto", "hold"):
                if cmd == "auto":
                    manager = self.mission_manager
                    if (
                        manager is None
                        or not manager.status.verified
                        or not manager.executable_items
                    ):
                        return False, "mission re-upload required before AUTO"
                    transport = manager.transport
                    self.home_valid = bool(
                        transport.refresh_home(self.home_valid, 1.0)
                    )
                    transport.sync_navigation(telemetry)
                    ready, reason = manager.refresh_execution_readiness(
                        telemetry,
                        self.home_valid,
                    )
                    if not ready:
                        return False, f"AUTO blocked: {reason}"
                    self.set_mode("AUTO", timeout=2.5)
                    manager.status.completed = False
                    telemetry.flight_mode = "auto"
                    telemetry.control_mode = "auto"
                    return True, "verified mission AUTO confirmed"
                self.set_mode(cmd.upper())
                telemetry.flight_mode = cmd
                return True, f"mode {cmd} sent"
            if cmd == "mission":
                return False, "mission requires full mission payload"
            if cmd in ("goto", "waypoint"):
                if abs(command.target_lat) < 0.000001 or abs(command.target_lng) < 0.000001:
                    return False, "invalid waypoint"
                if telemetry.gps_fix_type < 3 or telemetry.satellites_visible < 6 or abs(telemetry.lat) < 0.000001 or abs(telemetry.lng) < 0.000001:
                    return False, f"no rover GPS fix; gps={telemetry.gps_fix_type} sats={telemetry.satellites_visible}"
                self.goto(command.target_lat, command.target_lng, command.target_speed)
                return True, "waypoint sent"
            return False, f"unknown command {cmd}"
        except Exception as exc:
            return False, str(exc)

    def arm(self, arm: bool) -> tuple[bool, str]:
        with self.session_lock:
            self.conn.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                1 if arm else 0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
            ack = self.conn.recv_match(
                type="COMMAND_ACK",
                blocking=True,
                timeout=1.5,
            )
            if ack is None:
                return False, "arm command sent but no COMMAND_ACK from flight controller"
            result = int(getattr(ack, "result", -1))
            accepted = {
                int(getattr(mavutil.mavlink, "MAV_RESULT_ACCEPTED", 0)),
                int(getattr(mavutil.mavlink, "MAV_RESULT_IN_PROGRESS", 5)),
            }
            if result not in accepted:
                name = mavutil.mavlink.enums.get("MAV_RESULT", {}).get(result)
                label = getattr(name, "name", None) if name else None
                return False, f"flight controller rejected {'arm' if arm else 'disarm'}: {label or result}"
            return True, f"{'arm' if arm else 'disarm'} acknowledged by flight controller"

    def safe_arm(self) -> tuple[bool, str]:
        # Rover may start moving immediately if armed while AUTO has an active mission.
        # Force a non-moving mode and neutral RC before and after arming.
        for mode in ("HOLD", "MANUAL"):
            try:
                self.set_mode(mode)
                break
            except Exception:
                continue
        self.stop()
        time.sleep(0.15)
        ok, detail = self.arm(True)
        time.sleep(0.2)
        self.stop()
        return ok, detail

    def set_mode(self, mode: str, timeout: float = 2.5) -> str:
        with self.session_lock:
            requested = str(mode).strip().upper()
            mapping = self.conn.mode_mapping() or {}
            if requested not in mapping:
                raise RuntimeError(f"mode not available: {requested}")
            self.conn.set_mode(mapping[requested])

            deadline = time.monotonic() + max(0.01, float(timeout))
            observed = "UNKNOWN"
            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                heartbeat = self.conn.recv_match(
                    type="HEARTBEAT",
                    blocking=True,
                    timeout=remaining,
                )
                if heartbeat is None:
                    break
                source = getattr(heartbeat, "get_srcSystem", lambda: 0)() or 0
                if source and int(source) != int(self.target_system):
                    continue
                self.last_heartbeat = time.time()
                try:
                    observed = str(mavutil.mode_string_v10(heartbeat)).upper()
                except Exception:
                    observed = str(getattr(heartbeat, "custom_mode", "UNKNOWN"))
                if observed == requested:
                    return observed
            raise RuntimeError(
                f"mode {requested} not confirmed; flight controller reports {observed}"
            )

    def stop(self) -> None:
        with self.session_lock:
            for _ in range(3):
                self.rc_override(0, 0)
                time.sleep(0.03)

    def try_stop(self) -> bool:
        if not self.session_lock.acquire(blocking=False):
            return False
        try:
            self.stop()
            return True
        finally:
            self.session_lock.release()

    def manual(self, steering: int, throttle: int) -> None:
        for mode in ("MANUAL", "HOLD"):
            try:
                self.set_mode(mode)
                break
            except Exception:
                continue
        self.rc_override(steering, throttle)

    def neutral(self) -> None:
        with self.session_lock:
            self.rc_override(0, 0)

    def try_neutral(self) -> bool:
        if not self.session_lock.acquire(blocking=False):
            return False
        try:
            self.neutral()
            return True
        finally:
            self.session_lock.release()

    def load_rc_params(self) -> None:
        if self.rc_params_loaded:
            return
        self.steer_min = int(self.read_param("RC1_MIN", self.steer_min))
        self.steer_trim = int(self.read_param("RC1_TRIM", self.steer_trim))
        self.steer_max = int(self.read_param("RC1_MAX", self.steer_max))
        self.throttle_min = int(self.read_param("RC3_MIN", self.throttle_min))
        self.throttle_trim = int(self.read_param("RC3_TRIM", self.throttle_trim))
        self.throttle_max = int(self.read_param("RC3_MAX", self.throttle_max))
        self.rc_params_loaded = True
        print(
            "RC params "
            f"steer={self.steer_min}/{self.steer_trim}/{self.steer_max} "
            f"throttle={self.throttle_min}/{self.throttle_trim}/{self.throttle_max}"
        )

    def read_param(self, name: str, fallback: int) -> float:
        try:
            self.conn.mav.param_request_read_send(
                self.target_system,
                self.target_component,
                name.encode(),
                -1,
            )
            deadline = time.time() + 0.8
            while time.time() < deadline:
                msg = self.conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.15)
                if msg and str(msg.param_id).strip("\x00") == name:
                    return float(msg.param_value)
        except Exception:
            pass
        return float(fallback)

    def scale_rc(self, value: int, channel_min: int, channel_trim: int, channel_max: int) -> int:
        value = int(clamp(value, -100, 100))
        if value >= 0:
            span = max(0, channel_max - channel_trim)
            return int(channel_trim + span * value / 100)
        span = max(0, channel_trim - channel_min)
        return int(channel_trim + span * value / 100)

    def rc_override(self, steering: int, throttle: int) -> None:
        with self.session_lock:
            steer_pwm = self.scale_rc(steering, self.steer_min, self.steer_trim, self.steer_max)
            throttle_pwm = self.scale_rc(throttle, self.throttle_min, self.throttle_trim, self.throttle_max)
            ignore = 65535
            self.conn.mav.rc_channels_override_send(
                self.target_system,
                self.target_component,
                steer_pwm,
                ignore,
                throttle_pwm,
                ignore,
                ignore,
                ignore,
                ignore,
                ignore,
            )

    def goto(self, lat: float, lng: float, speed: float) -> None:
        try:
            self.set_mode("GUIDED")
        except Exception:
            pass
        if speed > 0:
            self.conn.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
                0,
                0,
                speed,
                -1,
                0,
                0,
                0,
                0,
            )
        type_mask = 0b0000111111111000
        self.conn.mav.set_position_target_global_int_send(
            int(time.time() * 1000) & 0xFFFFFFFF,
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            type_mask,
            int(lat * 1e7),
            int(lng * 1e7),
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )

    def upload_mission(
        self, raw_items: list[dict], telemetry: RoverTelemetry
    ):
        if not self.connect(timeout=0.5):
            raise RuntimeError("flight controller not connected")
        if self.mission_manager is None:
            self.mission_manager = RoverMissionManager(
                RoverMavlinkTransport(self),
                vehicle_system=self.target_system,
                vehicle_component=self.target_component,
                clear_before_upload=False,
            )
        items = [
            MissionItem.from_payload(item, seq=index)
            for index, item in enumerate(raw_items, 1)
        ]
        return self.mission_manager.upload_and_verify(
            items, telemetry, home_valid=self.home_valid
        )

    def queue_mission(
        self,
        command_id: str,
        raw_items: list[dict],
        telemetry: RoverTelemetry,
        *,
        source_timestamp=None,
    ):
        if len(raw_items) > 100:
            raise ValueError("mission exceeds maximum 100 executable items")
        items = [
            MissionItem.from_payload(item, seq=index)
            for index, item in enumerate(raw_items, 1)
        ]
        return self.mission_worker.submit(
            str(command_id),
            items,
            copy.deepcopy(telemetry),
            self.home_valid,
            source_timestamp=source_timestamp,
        )

    def drain_mission_results(self):
        return self.mission_worker.drain_completed()

    def _execute_mission_job(self, job, progress):
        with self.session_lock:
            if not self.connect(timeout=0.5):
                raise MissionError("flight controller not connected")
            transport = RoverMavlinkTransport(self, telemetry=job.telemetry)
            self.mission_manager = RoverMissionManager(
                transport,
                vehicle_system=self.target_system,
                vehicle_component=self.target_component,
                clear_before_upload=False,
            )
            return self.mission_manager.upload_and_verify(
                job.items,
                job.telemetry,
                home_valid=self.home_valid,
                progress=progress,
            )


class RoverMavlinkTransport:
    """Adapter that keeps RoverMavlink as the sole connection owner."""

    def __init__(self, rover: RoverMavlink, clock=None, telemetry=None):
        self.rover = rover
        self.clock = clock or time.monotonic
        self.telemetry = telemetry

    def recv(self, timeout: float):
        return self.rover.conn.recv_match(blocking=True, timeout=timeout)

    def send(self, message_type: str, **fields) -> None:
        conn = self.rover.conn
        mav = conn.mav
        system = self.rover.target_system
        # Address the component that emitted the heartbeat.  For this Rover
        # build that is component 1; component 0 is not a valid upload target.
        component = self.rover.target_component or 1
        mission_type = getattr(
            getattr(mavutil, "mavlink", None),
            "MAV_MISSION_TYPE_MISSION",
            0,
        )
        if message_type == "MISSION_CLEAR_ALL":
            try:
                mav.mission_clear_all_send(system, component, mission_type)
            except TypeError:
                mav.mission_clear_all_send(system, component)
        elif message_type == "MISSION_COUNT":
            try:
                mav.mission_count_send(
                    system, component, int(fields["count"]), mission_type
                )
            except TypeError:
                mav.mission_count_send(system, component, int(fields["count"]))
        elif message_type == "MISSION_ITEM_INT":
            item = fields["item"]
            try:
                mav.mission_item_int_send(
                    system,
                    component,
                    item.seq,
                    item.frame,
                    item.command,
                    1 if item.is_home else 0,
                    item.autocontinue,
                    item.param1,
                    item.param2,
                    item.param3,
                    item.param4,
                    item.x,
                    item.y,
                    item.z,
                    mission_type,
                )
            except TypeError:
                mav.mission_item_int_send(
                    system,
                    component,
                    item.seq,
                    item.frame,
                    item.command,
                    1 if item.is_home else 0,
                    item.autocontinue,
                    item.param1,
                    item.param2,
                    item.param3,
                    item.param4,
                    item.x,
                    item.y,
                    item.z,
                )
        elif message_type == "MISSION_REQUEST_LIST":
            try:
                mav.mission_request_list_send(system, component, mission_type)
            except TypeError:
                mav.mission_request_list_send(system, component)
        elif message_type == "MISSION_REQUEST_INT":
            try:
                mav.mission_request_int_send(
                    system, component, int(fields["seq"]), mission_type
                )
            except TypeError:
                mav.mission_request_int_send(
                    system, component, int(fields["seq"])
                )
        elif message_type == "MISSION_ACK":
            try:
                mav.mission_ack_send(
                    system,
                    component,
                    int(fields["result"]),
                    int(fields.get("mission_type", mission_type)),
                )
            except TypeError:
                mav.mission_ack_send(
                    system,
                    component,
                    int(fields["result"]),
                )
        elif message_type == "SET_MODE":
            self.rover.set_mode(str(fields["mode"]))
        else:
            raise ValueError(f"unsupported Rover MAVLink message {message_type}")

    def request_home_position(self) -> bool:
        now = self.clock()
        if now - self.rover.last_home_request < 1.0:
            return False
        self.rover.last_home_request = now
        self.rover.conn.mav.command_long_send(
            self.rover.target_system,
            self.rover.target_component,
            getattr(getattr(mavutil, "mavlink", None), "MAV_CMD_GET_HOME_POSITION", 410),
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        return True

    def refresh_home(self, current: bool, timeout: float) -> bool:
        self.request_home_position()
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            message = self.recv(max(0.0, deadline - self.clock()))
            if message is None:
                break
            getter = getattr(message, "get_type", None)
            kind = getter() if getter else ""
            self.dispatch(message)
            if kind != "HOME_POSITION":
                if self.rover.mission_manager is not None:
                    self.rover.mission_manager.observe(message)
                continue
            return self.rover.home_valid
        return bool(current or self.rover.home_valid)

    def dispatch(self, message) -> None:
        getter = getattr(message, "get_type", None)
        kind = getter() if getter else (
            message.get("type", "") if isinstance(message, dict) else ""
        )
        now = self.clock()
        telemetry = self.telemetry
        if kind == "GPS_RAW_INT" and telemetry is not None:
            telemetry.gps_fix_type = int(_message_field(message, "fix_type", 0) or 0)
            telemetry.satellites_visible = int(
                _message_field(message, "satellites_visible", 0) or 0
            )
            telemetry.gps_generation = self.rover.connection_generation
            telemetry.gps_updated_monotonic = now
            return
        if kind == "GLOBAL_POSITION_INT" and telemetry is not None:
            telemetry.lat = float(_message_field(message, "lat", 0) or 0) / 1e7
            telemetry.lng = float(_message_field(message, "lon", 0) or 0) / 1e7
            telemetry.position_observed = True
            telemetry.position_generation = self.rover.connection_generation
            telemetry.position_updated_monotonic = now
            return
        if kind == "EKF_STATUS_REPORT" and telemetry is not None:
            telemetry.ekf_flags = int(_message_field(message, "flags", 0) or 0)
            telemetry.ekf_generation = self.rover.connection_generation
            telemetry.ekf_updated_monotonic = now
            return
        if kind != "HOME_POSITION":
            return
        latitude = _message_field(message, "latitude", 0)
        longitude = _message_field(message, "longitude", 0)
        self.rover.home_valid = bool(
            abs(int(latitude or 0)) > 0 and abs(int(longitude or 0)) > 0
        )
        if self.rover.home_valid:
            self.rover.home_updated_monotonic = now
            if telemetry is not None:
                telemetry.home_generation = self.rover.connection_generation
                telemetry.home_updated_monotonic = now

    def sync_navigation(self, telemetry) -> None:
        telemetry.connection_generation = self.rover.connection_generation
        if self.rover.home_valid:
            telemetry.home_generation = self.rover.connection_generation
            telemetry.home_updated_monotonic = self.rover.home_updated_monotonic


def _message_field(message, name, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)
