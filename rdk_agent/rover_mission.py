"""Verified ArduPilot Rover mission upload using one MAVLink connection owner."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Iterable


MAV_MISSION_ACCEPTED = 0
MAV_CMD_NAV_WAYPOINT = 16
MAV_FRAME_GLOBAL = 0
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6


class MissionError(RuntimeError):
    pass


class MissionTimeout(MissionError):
    pass


class MissionDenied(MissionError):
    pass


class MissionVerificationError(MissionError):
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
    completed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class MissionResult:
    verified: bool
    execution_ready: bool
    item_count: int
    reason: str = ""


class RoverMissionManager:
    def __init__(
        self,
        transport,
        *,
        timeout: float = 1.0,
        retries: int = 2,
        altitude_tolerance: float = 0.25,
        coordinate_tolerance: int = 1,
    ):
        if retries < 0:
            raise ValueError("retries must be non-negative")
        self.transport = transport
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.altitude_tolerance = float(altitude_tolerance)
        self.coordinate_tolerance = int(coordinate_tolerance)
        self.status = MissionStatus()
        self.executable_items: list[MissionItem] = []

    def upload_and_verify(
        self,
        items: Iterable[MissionItem],
        telemetry,
        *,
        home_valid: bool,
    ) -> MissionResult:
        executable = list(items)
        if not executable:
            raise ValueError("mission requires at least one executable item")
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
        self.status = MissionStatus()

        self._clear()
        self._upload(upload_items)
        downloaded = self._download(len(upload_items))
        self._verify(upload_items, downloaded)

        ready, reason = self._execution_gate(telemetry, home_valid)
        self.executable_items = executable
        self.status.verified = True
        self.status.execution_ready = ready
        self.status.reason = reason
        return MissionResult(True, ready, len(executable), reason)

    def start_auto(self) -> None:
        if not self.status.verified or not self.status.execution_ready:
            raise RuntimeError("mission is not execution ready")
        self.status.completed = False
        self.transport.send("SET_MODE", mode="AUTO")

    def observe(self, message) -> None:
        kind = _field(message, "type", "")
        if kind == "MISSION_CURRENT":
            self.status.current_seq = int(_field(message, "seq", 0))
        elif kind == "MISSION_ITEM_REACHED":
            seq = int(_field(message, "seq", 0))
            self.status.reached_seq = seq
            if self.executable_items and seq >= self.executable_items[-1].seq:
                self.status.completed = True
                self.transport.send("SET_MODE", mode="HOLD")

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
            message = self._recv()
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
            return MissionItem(
                seq=actual_seq,
                frame=int(_field(message, "frame", -1)),
                command=int(_field(message, "command", -1)),
                x=int(_field(message, "x", 0)),
                y=int(_field(message, "y", 0)),
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
            if (
                abs(wanted.x - received.x) > self.coordinate_tolerance
                or abs(wanted.y - received.y) > self.coordinate_tolerance
                or abs(wanted.z - received.z) > self.altitude_tolerance
            ):
                raise MissionVerificationError(
                    f"readback mismatch seq {wanted.seq}: position"
                )

    @staticmethod
    def _execution_gate(telemetry, home_valid: bool) -> tuple[bool, str]:
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
        return (not missing, "" if not missing else "missing " + ", ".join(missing))

    def _recv(self):
        return self.transport.recv(self.timeout)

    def _recv_until(self, message_types: tuple[str, ...]):
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            message = self.transport.recv(remaining)
            if message is None:
                return None
            if _field(message, "type") in message_types:
                return message
            self.observe(message)


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
