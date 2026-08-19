"""Typed JSON datagrams for rover-to-node coordination."""

import hashlib
import json
import math
import uuid


PROTOCOL_VERSION = 1
MAX_DATAGRAM_BYTES = 4096
NODE_IDS = frozenset(("rover", "aircraft_1", "aircraft_2"))
MESSAGE_TYPES = frozenset((
    "command",
    "mission_begin",
    "mission_item",
    "mission_commit",
    "ack",
    "nack",
    "status",
))
COMMAND_ACTIONS = frozenset((
    "guided", "loiter", "auto", "land", "arm", "disarm",
))
TRANSACTION_STAGES = frozenset((
    "RECEIVED", "QUEUED", "EXECUTING", "VERIFIED", "FAILED",
))
REQUIRED_FIELDS = frozenset((
    "version",
    "type",
    "source",
    "target",
    "command_id",
    "sequence",
    "timestamp",
    "payload",
))


class NodeMessageError(ValueError):
    """Raised when a node datagram is malformed or misrouted."""


def _canonical_json(value):
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise NodeMessageError("message is not valid JSON") from exc


def _exact_fields(value, required, optional=()):
    if not isinstance(value, dict):
        raise NodeMessageError("payload must be an object")
    fields = set(value)
    required = set(required)
    allowed = required | set(optional)
    if not required <= fields or not fields <= allowed:
        raise NodeMessageError("invalid payload fields")


def _string(value, name):
    if not isinstance(value, str) or not value:
        raise NodeMessageError(f"{name} must be a non-empty string")


def _number(value, name, minimum=None, maximum=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise NodeMessageError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise NodeMessageError(f"{name} is below minimum")
    if maximum is not None and value > maximum:
        raise NodeMessageError(f"{name} exceeds maximum")


def _item_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise NodeMessageError("item_count must be an integer from 0 to 100")


def _item_index(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 100:
        raise NodeMessageError("index must be an integer from 0 to 99")


def _digest(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise NodeMessageError("digest must be lowercase SHA-256 hex")


def _validate_mission_item(item, expected_index):
    allowed = {
        "index", "lat", "lon", "alt", "command", "frame",
        "param1", "param2", "param3", "param4", "autocontinue",
    }
    _exact_fields(item, ("index", "lat", "lon", "alt"), allowed - {
        "index", "lat", "lon", "alt",
    })
    _item_index(item["index"])
    if item["index"] != expected_index:
        raise NodeMessageError("mission item index mismatch")
    _number(item["lat"], "lat", -90.0, 90.0)
    _number(item["lon"], "lon", -180.0, 180.0)
    _number(item["alt"], "alt", -1000.0, 100000.0)
    if "command" in item and (
        isinstance(item["command"], bool)
        or not isinstance(item["command"], int)
        or not 0 <= item["command"] <= 0xFFFF
    ):
        raise NodeMessageError("command must fit uint16")
    if "frame" in item and (
        isinstance(item["frame"], bool)
        or not isinstance(item["frame"], int)
        or not 0 <= item["frame"] <= 0xFF
    ):
        raise NodeMessageError("frame must fit uint8")
    for name in ("param1", "param2", "param3", "param4"):
        if name in item:
            _number(item[name], name)
    if "autocontinue" in item and not isinstance(item["autocontinue"], bool):
        raise NodeMessageError("autocontinue must be a boolean")


def _validate_payload(message_type, payload):
    if message_type == "command":
        _exact_fields(payload, ("action",), ("parameters",))
        if not isinstance(payload["action"], str):
            raise NodeMessageError("action must be a string")
        if payload["action"] not in COMMAND_ACTIONS:
            raise NodeMessageError("unsupported command action")
        if "parameters" in payload and not isinstance(payload["parameters"], dict):
            raise NodeMessageError("parameters must be an object")
    elif message_type == "status":
        required = (
            "online", "fc_connected", "blocked", "mode", "armed", "heartbeat_at",
            "lat", "lon", "position_observed", "altitude", "speed",
            "heading", "battery", "mission_stage", "mission_id", "fault",
            "link_state", "event",
        )
        _exact_fields(payload, required)
        for name in ("online", "fc_connected", "blocked", "armed", "position_observed"):
            if name in payload and not isinstance(payload[name], bool):
                raise NodeMessageError(f"{name} must be a boolean")
        for name in ("mode", "mission_stage", "link_state"):
            _string(payload[name], name)
        if payload["link_state"] not in ("ONLINE", "OFFLINE", "OPTICAL_BLOCKED"):
            raise NodeMessageError("invalid link_state")
        for name in ("mission_id", "fault"):
            if not isinstance(payload[name], str):
                raise NodeMessageError(f"{name} must be a string")
        for name in ("heartbeat_at", "altitude", "speed", "heading", "battery"):
            if name in payload:
                _number(payload[name], name)
        _number(payload["lat"], "lat", -90.0, 90.0)
        _number(payload["lon"], "lon", -180.0, 180.0)
        event = payload["event"]
        _exact_fields(event, ("timestamp", "sequence", "type", "text"))
        _number(event["timestamp"], "event.timestamp", 0.0)
        if (
            isinstance(event["sequence"], bool)
            or not isinstance(event["sequence"], int)
            or not 0 <= event["sequence"] <= 0xFFFFFFFF
        ):
            raise NodeMessageError("event.sequence must fit uint32")
        _string(event["type"], "event.type")
        if not isinstance(event["text"], str):
            raise NodeMessageError("event.text must be a string")
    elif message_type == "ack":
        _exact_fields(payload, ("stage",), ("detail", "duplicate"))
        if not isinstance(payload["stage"], str):
            raise NodeMessageError("stage must be a string")
        if payload["stage"] not in TRANSACTION_STAGES:
            raise NodeMessageError("invalid transaction stage")
        if "detail" in payload and not isinstance(payload["detail"], str):
            raise NodeMessageError("detail must be a string")
        if "duplicate" in payload and not isinstance(payload["duplicate"], bool):
            raise NodeMessageError("duplicate must be a boolean")
    elif message_type == "nack":
        _exact_fields(payload, ("reason",), ("stage",))
        _string(payload["reason"], "reason")
        if "stage" in payload:
            if not isinstance(payload["stage"], str):
                raise NodeMessageError("stage must be a string")
            if payload["stage"] not in TRANSACTION_STAGES:
                raise NodeMessageError("invalid transaction stage")
    elif message_type in ("mission_begin", "mission_commit"):
        _exact_fields(payload, ("mission_id", "item_count", "digest"))
        _string(payload["mission_id"], "mission_id")
        _item_count(payload["item_count"])
        _digest(payload["digest"])
    elif message_type == "mission_item":
        _exact_fields(payload, ("mission_id", "index", "item"))
        _string(payload["mission_id"], "mission_id")
        _item_index(payload["index"])
        _validate_mission_item(payload["item"], payload["index"])


def validate_node_message(message, expected_target=None):
    if not isinstance(message, dict):
        raise NodeMessageError("message must be an object")
    fields = set(message)
    if fields != REQUIRED_FIELDS:
        missing = REQUIRED_FIELDS - fields
        unknown = fields - REQUIRED_FIELDS
        detail = []
        if missing:
            detail.append("missing " + ",".join(sorted(missing)))
        if unknown:
            detail.append("unknown " + ",".join(sorted(unknown)))
        raise NodeMessageError("invalid fields: " + "; ".join(detail))
    if (
        isinstance(message["version"], bool)
        or not isinstance(message["version"], int)
        or message["version"] != PROTOCOL_VERSION
    ):
        raise NodeMessageError("unsupported version")
    if not isinstance(message["type"], str):
        raise NodeMessageError("message type must be a string")
    if message["type"] not in MESSAGE_TYPES:
        raise NodeMessageError("invalid message type")
    for field in ("source", "target"):
        if not isinstance(message[field], str):
            raise NodeMessageError(f"{field} must be a string")
        if message[field] not in NODE_IDS:
            raise NodeMessageError(f"invalid {field}")
    if expected_target is not None and message["target"] != expected_target:
        raise NodeMessageError("message target does not match receiver")
    try:
        uuid.UUID(message["command_id"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise NodeMessageError("command_id must be a UUID") from exc
    sequence = message["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise NodeMessageError("sequence must be an integer")
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise NodeMessageError("sequence must fit uint32")
    timestamp = message["timestamp"]
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
        or timestamp < 0
    ):
        raise NodeMessageError("timestamp must be a non-negative finite number")
    if not isinstance(message["payload"], dict):
        raise NodeMessageError("payload must be an object")
    _validate_payload(message["type"], message["payload"])
    _canonical_json(message)
    return message


def make_node_message(
    message_type,
    *,
    source,
    target,
    command_id,
    sequence,
    timestamp,
    payload,
):
    message = {
        "version": PROTOCOL_VERSION,
        "type": message_type,
        "source": source,
        "target": target,
        "command_id": command_id,
        "sequence": sequence,
        "timestamp": timestamp,
        "payload": payload,
    }
    return validate_node_message(message)


def build_command_message(*, action, parameters=None, **identity):
    if parameters is None:
        parameters = {}
    return make_node_message(
        "command", payload={"action": action, "parameters": parameters}, **identity
    )


def build_status_message(
    *, source, target, command_id, sequence, timestamp, online, **fields
):
    return make_node_message(
        "status",
        source=source,
        target=target,
        command_id=command_id,
        sequence=sequence,
        timestamp=timestamp,
        payload={"online": online, **fields},
    )


def build_ack_message(*, stage, duplicate=False, detail=None, **identity):
    payload = {"stage": stage, "duplicate": duplicate}
    if detail is not None:
        payload["detail"] = detail
    return make_node_message("ack", payload=payload, **identity)


def build_nack_message(*, reason, stage=None, **identity):
    payload = {"reason": reason}
    if stage is not None:
        payload["stage"] = stage
    return make_node_message("nack", payload=payload, **identity)


def build_mission_begin_message(*, mission_id, item_count, digest, **identity):
    return make_node_message(
        "mission_begin",
        payload={"mission_id": mission_id, "item_count": item_count, "digest": digest},
        **identity,
    )


def build_mission_item_message(*, mission_id, index, item, **identity):
    return make_node_message(
        "mission_item",
        payload={"mission_id": mission_id, "index": index, "item": item},
        **identity,
    )


def build_mission_commit_message(*, mission_id, item_count, digest, **identity):
    return make_node_message(
        "mission_commit",
        payload={"mission_id": mission_id, "item_count": item_count, "digest": digest},
        **identity,
    )


def encode_node_message(message, max_datagram_bytes=MAX_DATAGRAM_BYTES):
    if max_datagram_bytes <= 0:
        raise NodeMessageError("max datagram size must be positive")
    wire = _canonical_json(validate_node_message(message))
    if len(wire) > max_datagram_bytes:
        raise NodeMessageError("datagram exceeds size limit")
    return wire


def decode_node_message(data, expected_target=None, max_datagram_bytes=MAX_DATAGRAM_BYTES):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise NodeMessageError("datagram must be bytes-like")
    wire = bytes(data)
    if len(wire) > max_datagram_bytes:
        raise NodeMessageError("datagram exceeds size limit")
    try:
        message = json.loads(wire.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise NodeMessageError("datagram is not valid UTF-8 JSON") from exc
    return validate_node_message(message, expected_target=expected_target)


def mission_digest(items):
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise NodeMessageError("mission items must be a list of objects")
    return hashlib.sha256(_canonical_json(items)).hexdigest()
