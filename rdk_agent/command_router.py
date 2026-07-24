"""Typed cloud command normalization and vehicle-isolated routing."""

from dataclasses import dataclass
import json
import time
import uuid


class CommandRejected(ValueError):
    """Raised when a cloud command is unsafe or no longer actionable."""


def _flatten(data):
    if not isinstance(data, dict):
        raise TypeError("cloud command must be an object")
    return {
        key: value["value"] if isinstance(value, dict) and "value" in value else value
        for key, value in data.items()
    }


def _timestamp(value, fallback):
    if value in (None, ""):
        return float(fallback)
    result = float(value)
    if result > 10_000_000_000:
        result /= 1000.0
    return result


def _number_if_present(data, key):
    if key not in data or data[key] in (None, ""):
        return None
    return float(data[key])


@dataclass(frozen=True)
class CloudCommand:
    command_id: uuid.UUID
    source_timestamp: float
    target: str
    action: str
    payload: dict

    @classmethod
    def from_cloud(cls, raw, source="tuya", clock=None):
        del source  # The normalized shape intentionally contains no credentials.
        clock = clock or time.time
        data = _flatten(raw)
        raw_command = data.get("command", data.get("action", data.get("last_command")))
        if isinstance(raw_command, str) and raw_command.lstrip().startswith("{"):
            try:
                envelope = json.loads(raw_command)
            except json.JSONDecodeError:
                envelope = None
            if isinstance(envelope, dict):
                merged = dict(envelope)
                merged.update({key: value for key, value in data.items() if key != "command"})
                data = merged
        nested = data.get("payload")
        payload = dict(nested) if isinstance(nested, dict) else {}
        raw_action = str(
            data.get("action", data.get("command", data.get("last_command", "noop")))
            or "noop"
        ).strip().lower()
        explicit_target = str(data.get("target", "") or "").strip().lower()
        if raw_action in ("network_phone", "network_aircraft"):
            target, action = "system", "network_mode"
            payload["mode"] = raw_action[len("network_") :]
        elif raw_action.startswith("aircraft_"):
            target, action = "aircraft", raw_action[len("aircraft_") :]
        else:
            target = explicit_target or "rover"
            action = raw_action
        if target not in ("rover", "aircraft", "system"):
            raise ValueError("target must be rover, aircraft, or system")
        if not action:
            raise ValueError("action must not be empty")

        excluded = {
            "command_id", "source_timestamp", "timestamp", "time", "target",
            "action", "command", "last_command", "payload",
        }
        for key, value in data.items():
            if key not in excluded:
                payload[key] = value
        for key in ("target_lat", "target_lng", "target_speed"):
            numeric = _number_if_present(payload, key)
            if numeric is not None:
                payload[key] = numeric

        raw_id = data.get("command_id")
        command_id = uuid.UUID(str(raw_id)) if raw_id else uuid.uuid4()
        source_timestamp = _timestamp(
            data.get("source_timestamp", data.get("timestamp", data.get("time"))),
            clock(),
        )
        return cls(command_id, source_timestamp, target, action, payload)


class CommandRouter:
    def __init__(
        self,
        rover_executor,
        aircraft_link,
        optical_state,
        system_executor=None,
        clock=None,
        max_age_seconds=10.0,
    ):
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.rover_executor = rover_executor
        self.aircraft_link = aircraft_link
        self.system_executor = system_executor
        self.optical_state = optical_state
        self.clock = clock or time.time
        self.max_age_seconds = float(max_age_seconds)

    def route(self, command):
        if not isinstance(command, CloudCommand):
            raise TypeError("command must be a CloudCommand")
        age = self.clock() - command.source_timestamp
        if age > self.max_age_seconds:
            raise CommandRejected("stale command")
        if age < -self.max_age_seconds:
            raise CommandRejected("command timestamp is too far in the future")
        if command.target == "aircraft":
            if str(self.optical_state()).strip().lower() != "locked":
                raise CommandRejected("aircraft command rejected: optical link blocked")
            return self.aircraft_link.execute(command)
        if command.target == "system":
            if self.system_executor is None:
                raise CommandRejected("system commands are disabled")
            return self.system_executor.execute(command)
        return self.rover_executor.execute(command)


class RuntimeCommandAPI:
    """Callable boundary for a future full-mission ground-station endpoint."""

    def __init__(self, router, clock=None):
        self.router = router
        self.clock = clock or time.time

    def submit_mission(self, mission_payload, command_id=None):
        if not isinstance(mission_payload, dict):
            raise TypeError("mission payload must be an object")
        command = CloudCommand(
            command_id or uuid.uuid4(),
            self.clock(),
            "aircraft",
            "mission",
            dict(mission_payload),
        )
        return self.router.route(command)
