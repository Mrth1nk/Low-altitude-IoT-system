from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time

from .telemetry import field, message_type


MAV_MISSION_ACCEPTED = 0
MAV_MISSION_OPERATION_CANCELLED = 15
MAV_MISSION_TYPE_MISSION = 0
MAV_CMD_NAV_WAYPOINT = 16
MAV_FRAME_GLOBAL = 0
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
MAX_PROTOCOL_ITEMS = 100


class MissionError(RuntimeError):
    pass


class MissionTimeout(MissionError):
    pass


class MissionDenied(MissionError):
    pass


class MissionVerificationError(MissionError):
    pass


class MissionUnsafeResidual(MissionError):
    pass


@dataclass(frozen=True)
class MissionResult:
    verified: bool
    execution_ready: bool
    reason: str
    item_count: int


class AircraftMissionWorker:
    def __init__(
        self,
        session,
        telemetry,
        *,
        timeout=1.0,
        retries=2,
        operation_timeout=15.0,
        max_sequence_requests=5,
        coordinate_tolerance=1,
        altitude_tolerance=0.25,
        param_tolerance=1e-4,
        clock=time.monotonic,
    ):
        self.session = session
        self.telemetry = telemetry
        self.timeout = float(timeout)
        self.retries = int(retries)
        self.operation_timeout = float(operation_timeout)
        self.max_sequence_requests = int(max_sequence_requests)
        self.coordinate_tolerance = int(coordinate_tolerance)
        self.altitude_tolerance = float(altitude_tolerance)
        self.param_tolerance = float(param_tolerance)
        self.clock = clock
        self._deadline = float("inf")
        self._requests = {}

    def execute(self, record, *, start_auto=False):
        user_items = self._items_from_record(record)
        items = self._protocol_items(user_items)
        self._deadline = self.clock() + self.operation_timeout
        self._requests = {}
        with self.session.transaction() as transaction:
            try:
                self._clear(transaction)
                self._upload(transaction, items)
                downloaded = self._download(transaction, len(items))
                self._verify_protocol_home(items[0], downloaded[0])
                self._verify(items[1:], downloaded[1:])
            except Exception as original:
                if not self._cleanup(transaction):
                    raise MissionUnsafeResidual(
                        f"mission failed and cleanup was not acknowledged: {original}"
                    ) from original
                raise
            ready, reason = self.telemetry.execution_gate()
            result = MissionResult(True, ready, reason, len(user_items))
            if start_auto and ready:
                self.session.send("SET_MODE", mode="AUTO")
            return result

    def set_mode(self, mode):
        with self.session.transaction():
            self.session.send("SET_MODE", mode=str(mode).upper())

    def arm(self, value):
        with self.session.transaction():
            self.session.send("ARM", value=bool(value))

    def _clear(self, transaction):
        for _attempt in range(self.retries + 1):
            self.session.send(
                "MISSION_CLEAR_ALL", mission_type=MAV_MISSION_TYPE_MISSION
            )
            ack = self._receive(transaction, ("MISSION_ACK",))
            if ack is None:
                continue
            if self._ack_result(ack) != MAV_MISSION_ACCEPTED:
                raise MissionDenied("mission clear denied")
            return
        raise MissionTimeout("mission clear acknowledgement timeout")

    def _upload(self, transaction, items):
        self.session.send(
            "MISSION_COUNT",
            count=len(items),
            mission_type=MAV_MISSION_TYPE_MISSION,
        )
        attempts = 0
        while True:
            message = self._receive(
                transaction,
                ("MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"),
            )
            if message is None:
                if attempts >= self.retries:
                    raise MissionTimeout("mission item request timeout")
                attempts += 1
                self.session.send(
                    "MISSION_COUNT",
                    count=len(items),
                    mission_type=MAV_MISSION_TYPE_MISSION,
                )
                continue
            if message_type(message) == "MISSION_ACK":
                if self._ack_result(message) != MAV_MISSION_ACCEPTED:
                    raise MissionDenied("mission upload denied")
                return
            sequence = int(field(message, "seq", -1))
            if not 0 <= sequence < len(items):
                raise MissionDenied(f"invalid mission request seq={sequence}")
            count = self._requests.get(sequence, 0) + 1
            self._requests[sequence] = count
            if count > self.max_sequence_requests:
                raise MissionDenied(
                    f"mission request repeat limit seq={sequence}"
                )
            self.session.send(
                "MISSION_ITEM_INT",
                item=items[sequence],
                mission_type=MAV_MISSION_TYPE_MISSION,
            )
            attempts = 0

    def _download(self, transaction, expected_count):
        count = None
        for _attempt in range(self.retries + 1):
            self.session.send(
                "MISSION_REQUEST_LIST",
                mission_type=MAV_MISSION_TYPE_MISSION,
            )
            message = self._receive(transaction, ("MISSION_COUNT",))
            if message is not None:
                count = int(field(message, "count", -1))
                break
        if count is None:
            raise MissionTimeout("mission readback count timeout")
        if count != expected_count:
            raise MissionVerificationError(
                f"mission readback count expected={expected_count} actual={count}"
            )
        downloaded = []
        for sequence in range(count):
            downloaded.append(self._download_item(transaction, sequence))
        self.session.send(
            "MISSION_ACK",
            result=MAV_MISSION_ACCEPTED,
            mission_type=MAV_MISSION_TYPE_MISSION,
        )
        return downloaded

    def _download_item(self, transaction, sequence):
        for _attempt in range(self.retries + 1):
            self.session.send(
                "MISSION_REQUEST_INT",
                seq=sequence,
                mission_type=MAV_MISSION_TYPE_MISSION,
            )
            message = self._receive(
                transaction, ("MISSION_ITEM", "MISSION_ITEM_INT")
            )
            if message is None:
                continue
            if int(field(message, "seq", -1)) != sequence:
                continue
            is_float = message_type(message) == "MISSION_ITEM"
            raw_x = field(message, "x", 0)
            raw_y = field(message, "y", 0)
            return {
                "seq": sequence,
                "frame": int(field(message, "frame", -1)),
                "command": int(field(message, "command", -1)),
                "x": (
                    int(round(float(raw_x) * 1e7))
                    if is_float
                    else int(raw_x)
                ),
                "y": (
                    int(round(float(raw_y) * 1e7))
                    if is_float
                    else int(raw_y)
                ),
                "z": float(field(message, "z", 0.0)),
                "param1": float(field(message, "param1", 0.0)),
                "param2": float(field(message, "param2", 0.0)),
                "param3": float(field(message, "param3", 0.0)),
                "param4": float(field(message, "param4", 0.0)),
                "autocontinue": int(field(message, "autocontinue", 1)),
            }
        raise MissionTimeout(f"mission readback item timeout seq={sequence}")

    def _verify(self, expected, actual):
        for wanted, received in zip(expected, actual):
            sequence = wanted["seq"]
            if (
                wanted["command"] != received["command"]
                or wanted["frame"] != received["frame"]
            ):
                raise MissionVerificationError(
                    f"mission readback command/frame mismatch seq={sequence}"
                )
            if (
                abs(wanted["x"] - received["x"]) > self.coordinate_tolerance
                or abs(wanted["y"] - received["y"]) > self.coordinate_tolerance
                or abs(wanted["z"] - received["z"]) > self.altitude_tolerance
            ):
                raise MissionVerificationError(
                    f"mission readback position mismatch seq={sequence}"
                )
            for name in ("param1", "param2", "param3", "param4"):
                if (
                    abs(wanted[name] - received[name])
                    > self.param_tolerance
                ):
                    raise MissionVerificationError(
                        f"mission readback {name} mismatch seq={sequence}"
                    )
            if wanted["autocontinue"] != received["autocontinue"]:
                raise MissionVerificationError(
                    f"mission readback autocontinue mismatch seq={sequence}"
                )

    def _verify_protocol_home(self, wanted, received):
        if (
            int(received["seq"]) != 0
            or int(received["command"]) != MAV_CMD_NAV_WAYPOINT
            or int(received["frame"]) != MAV_FRAME_GLOBAL
        ):
            raise MissionVerificationError(
                "mission readback protocol Home command/frame mismatch seq=0"
            )
        if (
            abs(wanted["x"] - received["x"]) > self.coordinate_tolerance
            or abs(wanted["y"] - received["y"]) > self.coordinate_tolerance
            or abs(wanted["z"] - received["z"]) > self.altitude_tolerance
        ):
            raise MissionVerificationError(
                "mission readback protocol Home position mismatch seq=0"
            )

    def _receive(self, transaction, accepted):
        while True:
            remaining = self._deadline - self.clock()
            if remaining <= 0:
                raise MissionTimeout("mission global deadline exceeded")
            message = transaction.receive(
                accepted, min(self.timeout, remaining)
            )
            if message is None:
                return None
            if self._matches(message):
                return message

    def _matches(self, message):
        mission_type = field(message, "mission_type", None)
        if (
            mission_type is not None
            and int(mission_type) != MAV_MISSION_TYPE_MISSION
        ):
            return False
        target_system = field(message, "target_system", None)
        if (
            target_system is not None
            and int(target_system) != int(
                getattr(self.session, "gcs_system", 255)
            )
        ):
            return False
        target_component = field(message, "target_component", None)
        if (
            target_component is not None
            and int(target_component) != int(
                getattr(self.session, "gcs_component", 0)
            )
        ):
            return False
        source_system = field(message, "source_system", None)
        if source_system is None:
            getter = getattr(message, "get_srcSystem", None)
            source_system = getter() if getter else None
        if (
            source_system is not None
            and int(source_system) != int(
                getattr(self.session, "target_system", 1)
            )
        ):
            return False
        source_component = field(message, "source_component", None)
        if source_component is None:
            getter = getattr(message, "get_srcComponent", None)
            source_component = getter() if getter else None
        if (
            source_component is not None
            and int(source_component) != int(
                getattr(self.session, "target_component", 1)
            )
        ):
            return False
        return True

    def _cleanup(self, transaction):
        self.session.send(
            "MISSION_ACK",
            result=MAV_MISSION_OPERATION_CANCELLED,
            mission_type=MAV_MISSION_TYPE_MISSION,
        )
        self.session.send(
            "MISSION_CLEAR_ALL", mission_type=MAV_MISSION_TYPE_MISSION
        )
        cleanup_deadline = self.clock() + max(1.0, self.timeout)
        while self.clock() < cleanup_deadline:
            message = transaction.receive(
                ("MISSION_ACK",), cleanup_deadline - self.clock()
            )
            if message is None:
                return False
            if self._matches(message):
                return self._ack_result(message) == MAV_MISSION_ACCEPTED
        return False

    @staticmethod
    def _ack_result(message):
        return int(field(message, "result", field(message, "type", -1)))

    def _protocol_items(self, user_items):
        home = self.telemetry.snapshot()
        if home.get("home_valid"):
            x = int(home.get("home_lat", 0) or 0)
            y = int(home.get("home_lon", 0) or 0)
            z = float(home.get("home_alt", 0.0) or 0.0)
        else:
            x = y = 0
            z = 0.0
        protocol_home = {
            "seq": 0,
            "frame": MAV_FRAME_GLOBAL,
            "command": MAV_CMD_NAV_WAYPOINT,
            "x": x,
            "y": y,
            "z": z,
            "param1": 0.0,
            "param2": 0.0,
            "param3": 0.0,
            "param4": 0.0,
            "autocontinue": 1,
            "protocol_home": True,
        }
        return [
            protocol_home,
            *[
                {**item, "seq": index}
                for index, item in enumerate(user_items, start=1)
            ],
        ]

    @staticmethod
    def _items_from_record(record):
        if not isinstance(record, dict) or record.get("kind") != "mission":
            raise ValueError("complete durable mission record is required")
        if record.get("stage") != "MISSION_STAGED":
            raise ValueError("durable MISSION_STAGED record is required")
        raw_items = record.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("complete durable mission items are required")
        if len(raw_items) >= MAX_PROTOCOL_ITEMS:
            raise ValueError(
                "aircraft mission exceeds 99 user items plus protocol Home"
            )
        checksum = hashlib.sha256(
            json.dumps(
                raw_items,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if checksum != record.get("checksum"):
            raise ValueError("durable mission checksum mismatch")
        items = []
        for sequence, raw in enumerate(raw_items):
            command = int(raw.get("command", MAV_CMD_NAV_WAYPOINT))
            frame = int(raw.get("frame", MAV_FRAME_GLOBAL_RELATIVE_ALT_INT))
            if frame == MAV_FRAME_GLOBAL_RELATIVE_ALT_INT:
                frame = MAV_FRAME_GLOBAL_RELATIVE_ALT
            params = {
                name: float(raw.get(name, 0.0))
                for name in ("param1", "param2", "param3", "param4")
            }
            if command == MAV_CMD_NAV_WAYPOINT:
                for name, value in params.items():
                    if value != 0.0:
                        raise ValueError(
                            "unsupported simple NAV_WAYPOINT "
                            f"{name}={value}; params must be zero"
                        )
            items.append(
                {
                    "seq": sequence,
                    "frame": frame,
                    "command": command,
                    "x": int(round(float(raw["lat"]) * 1e7)),
                    "y": int(round(float(raw["lon"]) * 1e7)),
                    "z": float(raw["alt"]),
                    **params,
                    "autocontinue": int(bool(raw.get("autocontinue", True))),
                }
            )
        return items
