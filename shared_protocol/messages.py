"""Typed payload validation for LIOT messages."""

import math

from .frame import MessageType


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
        _object(payload, ("mission_id", "item_count", "vehicle"))
        _string(payload["mission_id"], "mission_id")
        _uint32(payload["item_count"], "item_count")
        if payload["vehicle"] not in ("aircraft", "rover"):
            raise ValueError("vehicle must be aircraft or rover")
    elif message_type is MessageType.MISSION_ITEM:
        _object(payload, ("mission_id", "index", "lat", "lon", "alt"))
        _string(payload["mission_id"], "mission_id")
        _uint32(payload["index"], "index")
        _number(payload["lat"], "lat", -90.0, 90.0)
        _number(payload["lon"], "lon", -180.0, 180.0)
        _number(payload["alt"], "alt")
    elif message_type is MessageType.MISSION_COMMIT:
        _object(payload, ("mission_id", "item_count"))
        _string(payload["mission_id"], "mission_id")
        _uint32(payload["item_count"], "item_count")
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
