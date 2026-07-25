from __future__ import annotations

import secrets
import json
import time
import uuid

from shared_protocol.auth import (
    AuthError,
    AuthenticatedStreamDecoder,
    PersistentAuthSession,
)
from shared_protocol.frame import (
    Frame,
    FrameDecoder,
    FrameError,
    MessageType,
    decode_frame,
    encode_frame,
)
from shared_protocol.mavlink_extension import (
    unwrap_liot_frame,
    wrap_liot_frame,
)

from .inbox import InboxRejected
from .optical_gate import OpticalBlocked


class MavlinkStreamDecoder:
    """Extract complete MAVLink 1/2 frames from the telemetry serial stream."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            start = next(
                (index for index, value in enumerate(self.buffer)
                 if value in (0xFD, 0xFE)),
                -1,
            )
            if start < 0:
                self.buffer.clear()
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 3:
                break
            is_v2 = self.buffer[0] == 0xFD
            header_len = 10 if is_v2 else 6
            signature_len = (
                13 if is_v2 and (self.buffer[2] & 0x01) else 0
            )
            frame_len = header_len + self.buffer[1] + 2 + signature_len
            if len(self.buffer) < frame_len:
                break
            frames.append(bytes(self.buffer[:frame_len]))
            del self.buffer[:frame_len]
        return frames


class AircraftLinkServer:
    """ELF serial-stream endpoint for authenticated LIOT frames."""

    MAVLINK_DOWNLINK_IDS = {
        11, 39, 40, 41, 43, 44, 45, 47, 51, 73, 76,
    }

    def __init__(
        self,
        inbox,
        optical_gate,
        *,
        psk,
        auth_store,
        stream=None,
        max_wire_bytes=65535,
        max_payload=8192,
        max_responses=8,
        clock=time.time,
        session_nonce=None,
        challenge=None,
        mavlink_sink=None,
        plaintext=False,
    ):
        if int(max_payload) < 1:
            raise ValueError("max_payload must be positive")
        if int(max_responses) < 1:
            raise ValueError("max_responses must be positive")
        self.inbox = inbox
        self.optical_gate = optical_gate
        self.stream = stream
        self.max_payload = int(max_payload)
        self.max_responses = int(max_responses)
        self.clock = clock
        self.auth = PersistentAuthSession(
            psk, auth_store, session_nonce=session_nonce
        )
        self.decoder = AuthenticatedStreamDecoder(
            max_wire_bytes=max_wire_bytes
        )
        self.plaintext = bool(plaintext)
        self.plain_decoder = FrameDecoder(max_payload_length=max_payload)
        self.mavlink_decoder = MavlinkStreamDecoder()
        challenge = secrets.token_bytes(32) if challenge is None else challenge
        if not isinstance(challenge, bytes) or len(challenge) != 32:
            raise ValueError("challenge must contain 32 bytes")
        self.challenge = challenge.hex()
        self.peer_session_nonce = None
        self._reported_results = set()
        self.sequence = 0
        self.mavlink_sink = mavlink_sink
        self.metrics = {
            "received": 0,
            "mavlink_chunks": 0,
            "mavlink_bytes": 0,
            "rejected_auth": 0,
            "last_auth_error": "",
            "rejected_frame": 0,
            "rejected_size": 0,
            "rejected_queue": 0,
            "serial_chunks": 0,
            "serial_bytes": 0,
            "serial_prefix": "",
        }

    def challenge_datagram(self):
        if self.plaintext:
            return b""
        return self._seal(
            self._frame(
                MessageType.AUTH_CHALLENGE,
                uuid.UUID(int=0),
                {"challenge": self.challenge},
            )
        )

    def feed_bytes(self, data):
        responses = []
        for packet in self.mavlink_decoder.feed(data):
            extension = unwrap_liot_frame(packet) if self.plaintext else None
            if extension is not None:
                for frame in self.plain_decoder.feed(extension):
                    responses.extend(self._handle_frame(frame))
                continue
            is_v2 = packet[0] == 0xFD
            message_id = (
                packet[7] | (packet[8] << 8) | (packet[9] << 16)
                if is_v2 else packet[5]
            )
            if (
                message_id in self.MAVLINK_DOWNLINK_IDS
                and self.mavlink_sink is not None
                and self.optical_gate.locked
            ):
                self.mavlink_sink(packet)
                self.metrics["mavlink_chunks"] += 1
                self.metrics["mavlink_bytes"] += len(packet)
        if self.plaintext:
            return responses[: self.max_responses]
        else:
            before = self.decoder.rejected_size
            envelopes = self.decoder.feed(data)
            self.metrics["rejected_size"] += self.decoder.rejected_size - before
            for envelope in envelopes:
                responses.extend(self._handle_envelope(envelope))
                if len(responses) >= self.max_responses:
                    return responses[: self.max_responses]
        return responses

    def _handle_frame(self, frame, nonce=None):
        if not self.plaintext and nonce is None:
            return []
        if self.plaintext:
            nonce = None
        self.metrics["received"] += 1
        if frame.message_type is MessageType.AUTH_RESPONSE:
            return []
        if not self.plaintext:
            if self.peer_session_nonce is None:
                self.peer_session_nonce = nonce
            elif nonce != self.peer_session_nonce:
                self.peer_session_nonce = None
                return [self.challenge_datagram()]
        if frame.message_type not in self.optical_gate.CLOUD_COMMAND_TYPES:
            return []
        try:
            self.optical_gate.require_locked(frame)
        except OpticalBlocked:
            return [self._wire(self.optical_gate.blocked_frame())]
        try:
            result = self.inbox.accept(frame)
        except InboxRejected as exc:
            self.metrics["rejected_queue"] += 1
            reason = exc.reason
            if exc.missing:
                reason += ":" + ",".join(str(item) for item in exc.missing)
            return [
                self._wire(
                    self._frame(
                        MessageType.NACK,
                        frame.command_id,
                        {"acked_sequence": frame.sequence, "reason": reason[:256]},
                    )
                )
            ]
        replies = [
            self._wire(
                self._frame(
                    MessageType.ACK,
                    frame.command_id,
                    {"acked_sequence": result.acked_sequence},
                )
            )
        ]
        if result.stage == "MISSION_STAGED":
            replies.append(
                self._wire(
                    self._frame(
                        MessageType.STATUS,
                        frame.command_id,
                        {
                            "state": "MISSION_STAGED",
                            "detail": str(frame.command_id),
                            "timestamp": float(self.clock()),
                        },
                    )
                )
            )
        return replies

    def pump_once(self, read_size=4096, snapshot=None):
        if self.stream is None:
            raise RuntimeError("serial byte stream is not configured")
        data = self.stream.read(int(read_size))
        if data:
            self.metrics["serial_chunks"] += 1
            self.metrics["serial_bytes"] += len(data)
            self.metrics["serial_prefix"] = bytes(data[:4]).hex()
        # The Wi-Fi serial downlink is the authenticated LIOT command channel.
        # Flight-controller MAVLink is owned exclusively by MavlinkSession; do
        # not forward transport envelopes as raw bytes to the flight controller.
        responses = self.feed_bytes(data or b"")
        responses.extend(self.poll_transaction_status())
        responses.extend(self.poll_status(snapshot))
        for response in responses:
            self.stream.write(response)
        return len(responses)

    def poll_status(self, snapshot=None):
        if self.plaintext:
            return []
        if not self.plaintext and self.peer_session_nonce is None:
            return []
        frame = self.optical_gate.due_frame(
            snapshot, now=float(self.clock())
        )
        return [] if frame is None else [self._wire(frame)]

    def poll_transaction_status(self):
        if not self.optical_gate.locked:
            return []
        responses = []
        for result in self.inbox.results:
            identity = (
                result.get("command_id", ""),
                result.get("stage", ""),
            )
            if identity in self._reported_results:
                continue
            detail = json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )[:96 if self.plaintext else 256]
            responses.append(
                self._wire(
                    self._frame(
                        MessageType.STATUS,
                        uuid.UUID(str(result["command_id"])),
                        {
                            "state": str(result["stage"])[:64],
                            "detail": detail,
                            "timestamp": float(self.clock()),
                        },
                    )
                )
            )
            self._reported_results.add(identity)
            if len(responses) >= self.max_responses:
                break
        return responses

    def _handle_envelope(self, envelope):
        try:
            payload, metadata = self.auth.open(envelope)
        except AuthError as exc:
            self.metrics["rejected_auth"] += 1
            self.metrics["last_auth_error"] = str(exc)[:96]
            return []
        try:
            frame = decode_frame(payload, self.max_payload)
        except FrameError:
            self.metrics["rejected_frame"] += 1
            return []

        nonce = metadata["session_nonce"]
        if frame.message_type is MessageType.AUTH_RESPONSE:
            if frame.payload["challenge"] != self.challenge:
                self.metrics["rejected_auth"] += 1
                return [self.challenge_datagram()]
            self.peer_session_nonce = nonce
            if not self.optical_gate.locked:
                return [self._seal(self.optical_gate.blocked_frame())]
            return [
                self._seal(
                    self._frame(
                        MessageType.STATUS,
                        uuid.UUID(int=0),
                        {
                            "state": "authenticated",
                            "detail": "serial_session_ready",
                            "timestamp": float(self.clock()),
                        },
                    )
                )
            ]
        return self._handle_frame(
            frame, nonce=metadata["session_nonce"]
        )

    def _frame(self, message_type, command_id, payload):
        frame = Frame(
            message_type,
            0,
            self.sequence,
            command_id,
            payload,
        )
        self.sequence = (self.sequence + 1) & 0xFFFFFFFF
        return frame

    def _seal(self, frame):
        return self.auth.seal(encode_frame(frame, self.max_payload))

    def _wire(self, frame):
        encoded = encode_frame(frame, self.max_payload)
        return (
            wrap_liot_frame(encoded)
            if self.plaintext
            else self.auth.seal(encoded)
        )
