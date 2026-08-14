"""Verified ArduPilot Rover mission upload using one MAVLink connection owner."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import queue
import threading
import time
from typing import Any, Iterable


MAV_MISSION_ACCEPTED = 0
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_DO_CHANGE_SPEED = 178
MAV_FRAME_GLOBAL = 0
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
MAV_MISSION_TYPE_MISSION = 0
MAV_MISSION_STATE_COMPLETE = 5
MAV_MISSION_OPERATION_CANCELLED = 15
MAX_EXECUTABLE_ITEMS = 100


class MissionError(RuntimeError):
    pass


class MissionTimeout(MissionError):
    pass


class MissionDenied(MissionError):
    pass


class MissionVerificationError(MissionError):
    pass


class MissionResidualUnsafe(MissionError):
    pass


@dataclass(frozen=True)
class MissionItem:
    seq: int
    frame: int
    command: int
    x: int
    y: int
    z: float
    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: float = 0.0
    autocontinue: int = 1
    is_home: bool = False

    @classmethod
    def from_payload(cls, item: dict[str, Any], seq: int) -> "MissionItem":
        lat = item.get("lat", item.get("x"))
        lon = item.get("lng", item.get("lon", item.get("y")))
        if lat is None or lon is None:
            raise ValueError(f"mission item {seq} requires lat/lng")
        x = int(round(float(lat) * 1e7)) if abs(float(lat)) <= 180 else int(lat)
        y = int(round(float(lon) * 1e7)) if abs(float(lon)) <= 180 else int(lon)
        return cls(
            seq=seq,
            frame=int(item.get("frame", MAV_FRAME_GLOBAL_RELATIVE_ALT_INT)),
            command=int(item.get("command", MAV_CMD_NAV_WAYPOINT)),
            x=x,
            y=y,
            z=float(item.get("alt", item.get("z", 0.0))),
            param1=float(item.get("param1", 0.0)),
            param2=float(item.get("param2", 0.0)),
            param3=float(item.get("param3", 0.0)),
            param4=float(item.get("param4", 0.0)),
            autocontinue=int(item.get("autocontinue", 1)),
        )


@dataclass
class MissionStatus:
    verified: bool = False
    execution_ready: bool = False
    current_seq: int = 0
    reached_seq: int = 0
    endpoint_reached: bool = False
    completed: bool = False
    residual_unsafe: bool = False
    reason: str = ""


@dataclass(frozen=True)
class MissionResult:
    verified: bool
    execution_ready: bool
    item_count: int
    reason: str = ""


@dataclass(frozen=True)
class MissionJob:
    command_id: str
    items: tuple
    telemetry: Any
    home_valid: bool
    source_timestamp: float


@dataclass(frozen=True)
class MissionJobStatus:
    command_id: str
    stage: str
    message: str = ""
    error_type: str = ""
    result: Any = None


class RoverMissionWorker:
    """Single FIFO mission worker with command-id-addressable results."""

    TERMINAL_STAGES = ("verified", "failed", "cancelled", "unsafe_residual")

    def __init__(
        self,
        operation,
        *,
        max_pending=4,
        max_history=64,
        max_age_seconds=10.0,
        wall_clock=None,
    ):
        if max_pending < 1 or max_history < 1 or max_age_seconds <= 0:
            raise ValueError("mission worker bounds must be positive")
        self.operation = operation
        self.max_pending = int(max_pending)
        self.max_history = int(max_history)
        self.max_age_seconds = float(max_age_seconds)
        self.wall_clock = wall_clock or time.time
        self._queue = queue.Queue(maxsize=self.max_pending)
        self._statuses = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False
        self._emitted = set()
        self._active_command_id = None
        self._latest_source_timestamp = float("-inf")
        self._thread = threading.Thread(
            target=self._run, name="rover-mission-worker", daemon=True
        )
        self._thread.start()

    def submit(
        self,
        command_id,
        items,
        telemetry,
        home_valid,
        *,
        source_timestamp=None,
    ):
        job = MissionJob(
            str(command_id),
            tuple(items),
            telemetry,
            bool(home_valid),
            float(self.wall_clock() if source_timestamp is None else source_timestamp),
        )
        with self._condition:
            if self._closed:
                raise RuntimeError("mission worker is closed")
            if job.command_id in self._statuses:
                return self._statuses[job.command_id]
            pending = sum(
                status.stage not in self.TERMINAL_STAGES
                for status in self._statuses.values()
            )
            if pending >= self.max_pending:
                raise MissionError("mission worker queue is full")
            status = MissionJobStatus(job.command_id, "queued")
            self._statuses[job.command_id] = status
            self._latest_source_timestamp = max(
                self._latest_source_timestamp, job.source_timestamp
            )
            self._queue.put_nowait(job)
            self._trim_history()
            self._condition.notify_all()
            return status

    def cancel(self, command_id):
        command_id = str(command_id)
        with self._condition:
            status = self._statuses.get(command_id)
            if status is None or status.stage in self.TERMINAL_STAGES:
                return False
            if command_id == self._active_command_id:
                return False
            self._statuses[command_id] = MissionJobStatus(
                command_id, "cancelled", "mission cancelled before execution"
            )
            self._condition.notify_all()
            return True

    def status(self, command_id):
        with self._condition:
            return self._statuses.get(str(command_id))

    def wait(self, command_id, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while True:
                status = self._statuses.get(str(command_id))
                if status and status.stage in self.TERMINAL_STAGES:
                    return status
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(f"mission job {command_id} not complete")
                self._condition.wait(remaining)

    def completed(self):
        with self._condition:
            return [
                status
                for status in self._statuses.values()
                if status.stage in self.TERMINAL_STAGES
            ]

    def drain_completed(self):
        with self._condition:
            ready = [
                status
                for command_id, status in self._statuses.items()
                if status.stage in self.TERMINAL_STAGES
                and command_id not in self._emitted
            ]
            self._emitted.update(status.command_id for status in ready)
            self._trim_history()
            return ready

    def close(self):
        with self._condition:
            self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=1)

    def _run(self):
        while True:
            job = self._queue.get()
            if job is None:
                return
            with self._condition:
                status = self._statuses.get(job.command_id)
                if status is None or status.stage == "cancelled":
                    continue
                if (
                    self.wall_clock() - job.source_timestamp > self.max_age_seconds
                    or job.source_timestamp < self._latest_source_timestamp
                ):
                    reason = (
                        "stale mission expired before execution"
                        if self.wall_clock() - job.source_timestamp > self.max_age_seconds
                        else "mission superseded before execution"
                    )
                    self._statuses[job.command_id] = MissionJobStatus(
                        job.command_id, "failed", reason, "MissionError"
                    )
                    self._condition.notify_all()
                    self._trim_history()
                    continue
                self._active_command_id = job.command_id
            self._set(MissionJobStatus(job.command_id, "uploading"))
            try:
                result = self.operation(
                    job,
                    lambda stage, message="": self._set(
                        MissionJobStatus(
                            job.command_id, str(stage), str(message)
                        )
                    ),
                )
                self._set(
                    MissionJobStatus(
                        job.command_id,
                        "verified",
                        result=getattr(result, "__dict__", result),
                    )
                )
            except Exception as exc:
                terminal_stage = (
                    "unsafe_residual"
                    if isinstance(exc, MissionResidualUnsafe)
                    else "failed"
                )
                self._set(
                    MissionJobStatus(
                        job.command_id,
                        terminal_stage,
                        message=str(exc),
                        error_type=type(exc).__name__,
                    )
                )
            finally:
                with self._condition:
                    self._active_command_id = None
                    self._trim_history()

    def _set(self, status):
        with self._condition:
            self._statuses[status.command_id] = status
            self._statuses.move_to_end(status.command_id)
            self._trim_history()
            self._condition.notify_all()

    def _trim_history(self):
        while len(self._statuses) > self.max_history:
            removable = next(
                (
                    command_id
                    for command_id, status in self._statuses.items()
                    if status.stage in self.TERMINAL_STAGES
                    and command_id != self._active_command_id
                ),
                None,
            )
            if removable is None:
                break
            self._statuses.pop(removable, None)
            self._emitted.discard(removable)


class RoverMissionManager:
    def __init__(
        self,
        transport,
        *,
        timeout: float = 1.0,
        retries: int = 2,
        altitude_tolerance: float = 0.25,
        coordinate_tolerance: int = 1,
        gcs_system: int = 255,
        gcs_component: int = 0,
        vehicle_system: int | None = None,
        vehicle_component: int | None = None,
        operation_timeout: float = 10.0,
        max_sequence_requests: int = 5,
        param_tolerance: float = 1e-4,
        clock=None,
        navigation_freshness: float = 3.0,
        clear_before_upload: bool = True,
    ):
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.transport = transport
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.altitude_tolerance = float(altitude_tolerance)
        self.coordinate_tolerance = int(coordinate_tolerance)
        self.gcs_system = int(gcs_system)
        self.gcs_component = int(gcs_component)
        self.vehicle_system = (
            None if vehicle_system is None else int(vehicle_system)
        )
        self.vehicle_component = (
            None if vehicle_component is None else int(vehicle_component)
        )
        self.operation_timeout = float(operation_timeout)
        self.max_sequence_requests = int(max_sequence_requests)
        self.param_tolerance = float(param_tolerance)
        self.clock = clock or time.monotonic
        self.navigation_freshness = float(navigation_freshness)
        self.clear_before_upload = bool(clear_before_upload)
        self._operation_deadline = float("inf")
        self._sequence_requests = {}
        self.status = MissionStatus()
        self.executable_items: list[MissionItem] = []

    def upload_and_verify(
        self,
        items: Iterable[MissionItem],
        telemetry,
        *,
        home_valid: bool,
        progress=None,
    ) -> MissionResult:
        executable = list(items)
        if not executable:
            raise ValueError("mission requires at least one executable item")
        if len(executable) > MAX_EXECUTABLE_ITEMS:
            raise ValueError(
                f"mission exceeds maximum {MAX_EXECUTABLE_ITEMS} executable items"
            )
        executable = [
            item if item.seq == index else MissionItem(**{**item.__dict__, "seq": index})
            for index, item in enumerate(executable, 1)
        ]
        home = MissionItem(
            seq=0,
            frame=MAV_FRAME_GLOBAL,
            command=MAV_CMD_NAV_WAYPOINT,
            x=0,
            y=0,
            z=0.0,
            is_home=True,
        )
        upload_items = [home, *executable]
        self.status = MissionStatus(residual_unsafe=self.status.residual_unsafe)
        self._operation_deadline = self.clock() + self.operation_timeout
        self._sequence_requests = {}
        try:
            if self.clear_before_upload:
                self._clear()
            self.status.residual_unsafe = False
            self._upload(upload_items)
            if progress is not None:
                progress("verifying", "reading mission back")
            downloaded = self._download(len(upload_items))
            self._verify(upload_items, downloaded)

            refresh_home = getattr(self.transport, "refresh_home", None)
            if refresh_home is not None:
                home_valid = bool(refresh_home(home_valid, self.timeout))
            sync_navigation = getattr(self.transport, "sync_navigation", None)
            if sync_navigation is not None:
                sync_navigation(telemetry)
            ready, reason = self._execution_gate(
                telemetry,
                home_valid,
                now=self.clock(),
                freshness=self.navigation_freshness,
            )
            self.executable_items = executable
            self.status.verified = True
            self.status.execution_ready = ready
            self.status.reason = reason
            return MissionResult(True, ready, len(executable), reason)
        except Exception as exc:
            self.status.verified = False
            self.status.execution_ready = False
            self.executable_items = []
            if not self._cancel_and_clear():
                self.status.residual_unsafe = True
                self.status.reason = "unsafe residual mission after failed cleanup"
                raise MissionResidualUnsafe(
                    f"{exc}; unsafe residual mission cleanup not acknowledged"
                ) from exc
            self.status.residual_unsafe = False
            raise

    def start_auto(self) -> None:
        if self.status.residual_unsafe:
            raise RuntimeError("unsafe residual mission blocks AUTO")
        if not self.status.verified or not self.status.execution_ready:
            raise RuntimeError("mission is not execution ready")
        self.status.completed = False
        self.transport.send("SET_MODE", mode="AUTO")

    def refresh_execution_readiness(
        self,
        telemetry,
        home_valid: bool,
        *,
        now: float | None = None,
    ) -> tuple[bool, str]:
        if self.status.residual_unsafe:
            raise RuntimeError("unsafe residual mission blocks AUTO")
        if not self.status.verified or not self.executable_items:
            raise RuntimeError("mission re-upload required")
        ready, reason = self._execution_gate(
            telemetry,
            home_valid,
            now=self.clock() if now is None else float(now),
            freshness=self.navigation_freshness,
        )
        self.status.execution_ready = ready
        self.status.reason = reason
        return ready, reason

    def observe(self, message) -> None:
        kind = _field(message, "type", "")
        if kind == "MISSION_CURRENT":
            self.status.current_seq = int(_field(message, "seq", 0))
            if int(_field(message, "mission_state", 0) or 0) == MAV_MISSION_STATE_COMPLETE:
                self.status.completed = True
        elif kind == "MISSION_ITEM_REACHED":
            seq = int(_field(message, "seq", 0))
            self.status.reached_seq = seq
            if self.executable_items and seq >= self.executable_items[-1].seq:
                self.status.endpoint_reached = True
        elif kind == "STATUSTEXT":
            text = str(_field(message, "text", "")).strip().lower()
            if self.status.endpoint_reached and "mission complete" in text:
                self.status.completed = True

    def publish_status(self, telemetry) -> None:
        if self.status.completed:
            telemetry.mission_status = (
                f"mission complete seq={self.status.reached_seq}"
            )
        elif self.status.endpoint_reached:
            telemetry.mission_status = (
                f"endpoint reached seq={self.status.reached_seq}"
            )
        elif self.status.current_seq:
            telemetry.mission_status = (
                f"mission active seq={self.status.current_seq}"
            )

    def _clear(self) -> None:
        for _attempt in range(self.retries + 1):
            self.transport.send("MISSION_CLEAR_ALL")
            message = self._recv_until(("MISSION_ACK",))
            if message is None:
                continue
            result = _ack_result(message)
            if result != MAV_MISSION_ACCEPTED:
                raise MissionDenied(f"mission clear denied ack={result}")
            return
        raise MissionTimeout("timeout waiting for MISSION_CLEAR_ALL acknowledgement")

    def _upload(self, items: list[MissionItem]) -> None:
        by_seq = {item.seq: item for item in items}
        attempts = 0
        self.transport.send("MISSION_COUNT", count=len(items))
        while True:
            message = self._recv_until(
                ("MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK")
            )
            if message is None:
                if attempts >= self.retries:
                    raise MissionTimeout("timeout waiting for mission item request")
                attempts += 1
                self.transport.send("MISSION_COUNT", count=len(items))
                continue
            kind = _field(message, "type")
            if kind in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                seq = int(_field(message, "seq", -1))
                if seq not in by_seq:
                    raise MissionDenied(f"flight controller requested invalid seq={seq}")
                count = self._sequence_requests.get(seq, 0) + 1
                self._sequence_requests[seq] = count
                if count > self.max_sequence_requests:
                    raise MissionError(
                        f"mission request limit exceeded seq={seq}"
                    )
                self.transport.send("MISSION_ITEM_INT", item=by_seq[seq])
                attempts = 0
                continue
            if kind == "MISSION_ACK":
                result = _ack_result(message)
                if result != MAV_MISSION_ACCEPTED:
                    raise MissionDenied(f"mission upload denied ack={result}")
                return

    def _download(self, expected_count: int) -> list[MissionItem]:
        count = None
        for _attempt in range(self.retries + 1):
            self.transport.send("MISSION_REQUEST_LIST")
            message = self._recv_until(("MISSION_COUNT",))
            if message is not None:
                count = int(_field(message, "count", -1))
                break
        if count is None:
            raise MissionTimeout("timeout waiting for mission readback count")
        if count != expected_count:
            raise MissionVerificationError(
                f"readback count mismatch expected={expected_count} actual={count}"
            )

        downloaded: list[MissionItem] = []
        for seq in range(count):
            item = self._download_item(seq)
            downloaded.append(item)
        self.transport.send(
            "MISSION_ACK",
            result=MAV_MISSION_ACCEPTED,
            mission_type=MAV_MISSION_TYPE_MISSION,
        )
        return downloaded

    def _download_item(self, seq: int) -> MissionItem:
        for _attempt in range(self.retries + 1):
            self.transport.send("MISSION_REQUEST_INT", seq=seq)
            message = self._recv_until(("MISSION_ITEM", "MISSION_ITEM_INT"))
            if message is None:
                continue
            actual_seq = int(_field(message, "seq", -1))
            if actual_seq != seq:
                continue
            kind = _field(message, "type")
            raw_x = _field(message, "x", 0)
            raw_y = _field(message, "y", 0)
            x = (
                int(round(float(raw_x) * 1e7))
                if kind == "MISSION_ITEM"
                else int(raw_x)
            )
            y = (
                int(round(float(raw_y) * 1e7))
                if kind == "MISSION_ITEM"
                else int(raw_y)
            )
            return MissionItem(
                seq=actual_seq,
                frame=int(_field(message, "frame", -1)),
                command=int(_field(message, "command", -1)),
                x=x,
                y=y,
                z=float(_field(message, "z", 0.0)),
                param1=float(_field(message, "param1", 0.0)),
                param2=float(_field(message, "param2", 0.0)),
                param3=float(_field(message, "param3", 0.0)),
                param4=float(_field(message, "param4", 0.0)),
                autocontinue=int(_field(message, "autocontinue", 1)),
                is_home=seq == 0,
            )
        raise MissionTimeout(f"timeout reading mission item seq={seq}")

    def _verify(
        self, expected: list[MissionItem], actual: list[MissionItem]
    ) -> None:
        for wanted, received in zip(expected, actual):
            if wanted.command != received.command or wanted.frame != received.frame:
                raise MissionVerificationError(
                    f"readback mismatch seq {wanted.seq}: command/frame"
                )
            if wanted.seq == 0:
                continue
            position_mismatch = (
                abs(wanted.x - received.x) > self.coordinate_tolerance
                or abs(wanted.y - received.y) > self.coordinate_tolerance
                or abs(wanted.z - received.z) > self.altitude_tolerance
            )
            if position_mismatch and wanted.command != MAV_CMD_DO_CHANGE_SPEED:
                raise MissionVerificationError(
                    f"readback mismatch seq {wanted.seq}: position"
                )
            for name in ("param1", "param2", "param3", "param4"):
                if abs(getattr(wanted, name) - getattr(received, name)) > self.param_tolerance:
                    raise MissionVerificationError(
                        f"readback mismatch seq {wanted.seq}: {name}"
                    )
            if wanted.autocontinue != received.autocontinue:
                raise MissionVerificationError(
                    f"readback mismatch seq {wanted.seq}: autocontinue"
                )

    @staticmethod
    def _execution_gate(
        telemetry,
        home_valid: bool,
        *,
        now: float | None = None,
        freshness: float = 3.0,
    ) -> tuple[bool, str]:
        checks = (
            (int(getattr(telemetry, "gps_fix_type", 0)) >= 3, "GPS fix"),
            (int(getattr(telemetry, "satellites_visible", 0)) >= 6, "satellites"),
            (
                abs(float(getattr(telemetry, "lat", 0.0))) > 0.000001
                and abs(float(getattr(telemetry, "lng", 0.0))) > 0.000001,
                "location",
            ),
            (bool(home_valid), "Home"),
            ((int(getattr(telemetry, "ekf_flags", 0)) & 0x11) == 0x11, "EKF"),
        )
        missing = [name for ok, name in checks if not ok]
        generation = int(getattr(telemetry, "connection_generation", 0) or 0)
        if generation:
            for label, generation_field, timestamp_field in (
                ("GPS", "gps_generation", "gps_updated_monotonic"),
                ("position", "position_generation", "position_updated_monotonic"),
                ("EKF", "ekf_generation", "ekf_updated_monotonic"),
                ("Home", "home_generation", "home_updated_monotonic"),
            ):
                if int(getattr(telemetry, generation_field, 0) or 0) != generation:
                    missing.append(f"{label} generation")
                    continue
                timestamp = float(getattr(telemetry, timestamp_field, 0.0) or 0.0)
                if now is None or timestamp <= 0 or now - timestamp > freshness:
                    missing.append(f"{label} stale")
        return (not missing, "" if not missing else "missing " + ", ".join(missing))

    def _recv(self):
        return self._recv_matching(None)

    def _recv_until(self, message_types: tuple[str, ...]):
        return self._recv_matching(message_types)

    def _recv_matching(self, message_types: tuple[str, ...] | None):
        deadline = min(self.clock() + self.timeout, self._operation_deadline)
        while True:
            self._ensure_operation_budget()
            remaining = deadline - self.clock()
            if remaining <= 0:
                return None
            message = self.transport.recv(remaining)
            if message is None:
                return None
            if not self._matches_transaction(message):
                continue
            dispatch = getattr(self.transport, "dispatch", None)
            if dispatch is not None:
                dispatch(message)
            if message_types is None or _field(message, "type") in message_types:
                return message
            self.observe(message)

    def _ensure_operation_budget(self):
        if self.clock() >= self._operation_deadline:
            raise MissionTimeout("mission operation deadline exceeded")

    def _cancel_and_clear(self):
        try:
            self.transport.send(
                "MISSION_ACK",
                result=MAV_MISSION_OPERATION_CANCELLED,
                mission_type=MAV_MISSION_TYPE_MISSION,
            )
            for _drained in range(256):
                message = self.transport.recv(0.0)
                if message is None:
                    break
                if not self._matches_transaction(message):
                    continue
                dispatch = getattr(self.transport, "dispatch", None)
                if dispatch is not None:
                    dispatch(message)
            for _attempt in range(self.retries + 1):
                self.transport.send("MISSION_CLEAR_ALL")
                cleanup_deadline = self.clock() + max(1.0, self.timeout)
                while self.clock() < cleanup_deadline:
                    message = self.transport.recv(
                        max(0.0, cleanup_deadline - self.clock())
                    )
                    if message is None:
                        break
                    if not self._matches_transaction(message):
                        continue
                    dispatch = getattr(self.transport, "dispatch", None)
                    if dispatch is not None:
                        dispatch(message)
                    if _field(message, "type") != "MISSION_ACK":
                        continue
                    if _ack_result(message) == MAV_MISSION_ACCEPTED:
                        return True
                    break
        except Exception:
            return False
        return False

    def _matches_transaction(self, message) -> bool:
        mission_type = _optional_field(message, "mission_type")
        if (
            mission_type is not None
            and int(mission_type) != MAV_MISSION_TYPE_MISSION
        ):
            return False
        target_system = _optional_field(message, "target_system")
        if target_system is not None and int(target_system) != self.gcs_system:
            return False
        target_component = _optional_field(message, "target_component")
        if (
            target_component is not None
            and int(target_component) != self.gcs_component
        ):
            return False
        source_system = _source_field(message, "system")
        if (
            source_system is not None
            and self.vehicle_system is not None
            and int(source_system) != self.vehicle_system
        ):
            return False
        source_component = _source_field(message, "component")
        if (
            source_component is not None
            and self.vehicle_component is not None
            and int(source_component) != self.vehicle_component
        ):
            return False
        return True


def _field(message, name: str, default=None):
    if isinstance(message, dict):
        return message.get(name, default)
    if name == "type":
        getter = getattr(message, "get_type", None)
        return getter() if getter else default
    return getattr(message, name, default)


def _ack_result(message) -> int:
    if isinstance(message, dict):
        return int(message.get("result", message.get("type_code", -1)))
    return int(getattr(message, "result", getattr(message, "type", -1)))


def _optional_field(message, name: str):
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _source_field(message, part: str):
    if isinstance(message, dict):
        return message.get(f"source_{part}")
    method = getattr(
        message,
        "get_srcSystem" if part == "system" else "get_srcComponent",
        None,
    )
    return method() if method else None
