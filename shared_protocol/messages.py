"""Typed payload validation for LIOT messages."""

import math

from .frame import MessageType


MAX_MISSION_ITEMS = 100


def _object(payload, required, optional=()):
    if not isinstance(payload, dict):
        raise TypeError("payload must be an object")
    allowed = set(required) | set(optional)
    missing = set(required) - set(payload)
    unknown = set(payload) - allowed
    if missing:
        raise ValueError("missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise ValueError("unknown fields: " + ", ".join(sorted(unknown)))


def _string(value, name, allow_empty=False):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")


def _uint32(value, name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"{name} must fit uint32")


def _uint16(value, name):
    _uint32(value, name)
    if value > 0xFFFF:
        raise ValueError(f"{name} must fit uint16")


def _checksum(value):
    _string(value, "checksum")
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("checksum must be lowercase SHA-256 hex")


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} is below minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds maximum")


def validate_payload(message_type, payload):
    message_type = MessageType(message_type)

    if message_type is MessageType.COMMAND:
        _object(payload, ("action",), ("parameters", "mode"))
        _string(payload["action"], "action")
        if "parameters" in payload and not isinstance(payload["parameters"], dict):
            raise TypeError("parameters must be an object")
        if "mode" in payload:
            _string(payload["mode"], "mode")
    elif message_type is MessageType.MISSION_BEGIN:
        _object(payload, ("mission_id", "item_count", "vehicle"), ("checksum",))
        _string(payload["mission_id"], "mission_id")
        _uint32(payload["item_count"], "item_count")
        if payload["item_count"] > MAX_MISSION_ITEMS:
            raise ValueError("item_count exceeds mission limit")
        if payload["vehicle"] not in ("aircraft", "rover"):
            raise ValueError("vehicle must be aircraft or rover")
        if "checksum" in payload:
            _checksum(payload["checksum"])
    elif message_type is MessageType.MISSION_ITEM:
        mavlink_fields = (
            "command", "frame", "param1", "param2", "param3", "param4",
            "autocontinue",
        )
        _object(
            payload,
            ("mission_id", "index", "lat", "lon", "alt"),
            mavlink_fields,
        )
        _string(payload["mission_id"], "mission_id")
        _uint32(payload["index"], "index")
        if payload["index"] >= MAX_MISSION_ITEMS:
            raise ValueError("index exceeds mission limit")
        _number(payload["lat"], "lat", -90.0, 90.0)
        _number(payload["lon"], "lon", -180.0, 180.0)
        _number(payload["alt"], "alt", -1000.0, 100000.0)
        if "command" in payload:
            _uint16(payload["command"], "command")
        if "frame" in payload:
            if isinstance(payload["frame"], bool) or not isinstance(payload["frame"], int):
                raise TypeError("frame must be an integer")
            if not 0 <= payload["frame"] <= 255:
                raise ValueError("frame must fit uint8")
        for name in ("param1", "param2", "param3", "param4"):
            if name in payload:
                _number(payload[name], name, -1000000.0, 1000000.0)
        if "autocontinue" in payload and not isinstance(payload["autocontinue"], bool):
            raise TypeError("autocontinue must be a boolean")
    elif message_type is MessageType.MISSION_COMMIT:
        _object(payload, ("mission_id", "item_count"), ("checksum",))
        _string(payload["mission_id"], "mission_id")
        _uint32(payload["item_count"], "item_count")
        if payload["item_count"] > MAX_MISSION_ITEMS:
            raise ValueError("item_count exceeds mission limit")
        if "checksum" in payload:
            _checksum(payload["checksum"])
    elif message_type is MessageType.ACK:
        _object(payload, ("acked_sequence",))
        _uint32(payload["acked_sequence"], "acked_sequence")
    elif message_type is MessageType.NACK:
        _object(payload, ("acked_sequence", "reason"))
        _uint32(payload["acked_sequence"], "acked_sequence")
        _string(payload["reason"], "reason")
    elif message_type is MessageType.STATUS:
        _object(payload, ("state",), ("detail",))
        _string(payload["state"], "state")
        if "detail" in payload:
            _string(payload["detail"], "detail", allow_empty=True)
    elif message_type is MessageType.LINK_BLOCKED:
        _object(payload, ("reason",))
        _string(payload["reason"], "reason")
    return payload
