"""Binary framing and streaming decode for the LIOT protocol."""

from dataclasses import dataclass
from enum import IntEnum
import json
import struct
import uuid
import zlib


MAGIC = b"LIOT"
VERSION = 1
HEADER = struct.Struct(">4sBBB I 16s I")
CRC = struct.Struct(">I")
HEADER_SIZE = HEADER.size
CRC_SIZE = CRC.size
DEFAULT_MAX_PAYLOAD_LENGTH = 1024 * 1024


class FrameError(ValueError):
    """Raised when a wire frame is malformed."""


class MessageType(IntEnum):
    COMMAND = 1
    MISSION_BEGIN = 2
    MISSION_ITEM = 3
    MISSION_COMMIT = 4
    ACK = 5
    NACK = 6
    STATUS = 7
    LINK_BLOCKED = 8


@dataclass(frozen=True)
class Frame:
    message_type: MessageType
    flags: int
    sequence: int
    command_id: uuid.UUID
    payload: dict

    def __post_init__(self):
        object.__setattr__(self, "message_type", MessageType(self.message_type))
        if not 0 <= self.flags <= 0xFF:
            raise ValueError("flags must fit uint8")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise ValueError("sequence must fit uint32")
        if not isinstance(self.command_id, uuid.UUID):
            raise TypeError("command_id must be a UUID")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be an object")


def _canonical_payload(message_type, payload):
    from .messages import validate_payload

    validate_payload(message_type, payload)
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("payload is not valid JSON") from exc


def encode_frame(frame, max_payload_length=DEFAULT_MAX_PAYLOAD_LENGTH):
    if not isinstance(frame, Frame):
        raise TypeError("frame must be a Frame")
    if max_payload_length < 0:
        raise ValueError("max_payload_length must be non-negative")
    payload = _canonical_payload(frame.message_type, frame.payload)
    if len(payload) > max_payload_length:
        raise FrameError("payload length exceeds limit")
    header = HEADER.pack(
        MAGIC,
        VERSION,
        int(frame.message_type),
        frame.flags,
        frame.sequence,
        frame.command_id.bytes,
        len(payload),
    )
    body = header + payload
    return body + CRC.pack(zlib.crc32(body) & 0xFFFFFFFF)


def decode_frame(data, max_payload_length=DEFAULT_MAX_PAYLOAD_LENGTH):
    if len(data) < HEADER_SIZE + CRC_SIZE:
        raise FrameError("frame is truncated")
    magic, version, raw_type, flags, sequence, command_bytes, payload_length = (
        HEADER.unpack_from(data)
    )
    if magic != MAGIC:
        raise FrameError("bad magic")
    if version != VERSION:
        raise FrameError("unsupported version")
    if payload_length > max_payload_length:
        raise FrameError("payload length exceeds limit")
    expected_length = HEADER_SIZE + payload_length + CRC_SIZE
    if len(data) != expected_length:
        raise FrameError("frame length mismatch")
    expected_crc = CRC.unpack_from(data, expected_length - CRC_SIZE)[0]
    actual_crc = zlib.crc32(data[:-CRC_SIZE]) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise FrameError("bad CRC")
    try:
        message_type = MessageType(raw_type)
        payload = json.loads(data[HEADER_SIZE:-CRC_SIZE].decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError("invalid type or JSON payload") from exc
    try:
        frame = Frame(
            message_type,
            flags,
            sequence,
            uuid.UUID(bytes=command_bytes),
            payload,
        )
        from .messages import validate_payload

        validate_payload(message_type, payload)
        return frame
    except (TypeError, ValueError) as exc:
        raise FrameError("invalid frame payload") from exc


class FrameDecoder:
    """Incrementally decodes frames and scans forward after corrupt input."""

    def __init__(self, max_payload_length=DEFAULT_MAX_PAYLOAD_LENGTH):
        if max_payload_length < 0:
            raise ValueError("max_payload_length must be non-negative")
        self.max_payload_length = max_payload_length
        self._buffer = bytearray()

    def feed(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        self._buffer.extend(data)
        frames = []
        while True:
            start = self._buffer.find(MAGIC)
            if start < 0:
                keep = min(len(self._buffer), len(MAGIC) - 1)
                if keep:
                    self._buffer[:] = self._buffer[-keep:]
                else:
                    self._buffer.clear()
                break
            if start:
                del self._buffer[:start]
            if len(self._buffer) < HEADER_SIZE:
                break
            try:
                magic, version, raw_type, _, _, _, payload_length = HEADER.unpack_from(
                    self._buffer
                )
                MessageType(raw_type)
            except (struct.error, ValueError):
                del self._buffer[0]
                continue
            if (
                magic != MAGIC
                or version != VERSION
                or payload_length > self.max_payload_length
            ):
                del self._buffer[0]
                continue
            frame_length = HEADER_SIZE + payload_length + CRC_SIZE
            if len(self._buffer) < frame_length:
                next_start = self._complete_frame_start_after_current()
                if next_start is not None:
                    del self._buffer[:next_start]
                    continue
                break
            candidate = bytes(self._buffer[:frame_length])
            try:
                frame = decode_frame(candidate, self.max_payload_length)
            except FrameError:
                del self._buffer[0]
                continue
            frames.append(frame)
            del self._buffer[:frame_length]
        return frames

    def _complete_frame_start_after_current(self):
        start = self._buffer.find(MAGIC, 1)
        while start >= 0:
            if len(self._buffer) - start < HEADER_SIZE + CRC_SIZE:
                return None
            try:
                payload_length = HEADER.unpack_from(self._buffer, start)[-1]
            except struct.error:
                return None
            candidate_length = HEADER_SIZE + payload_length + CRC_SIZE
            if (
                payload_length <= self.max_payload_length
                and len(self._buffer) - start >= candidate_length
            ):
                candidate = bytes(self._buffer[start : start + candidate_length])
                try:
                    decode_frame(candidate, self.max_payload_length)
                    return start
                except FrameError:
                    pass
            start = self._buffer.find(MAGIC, start + 1)
        return None
