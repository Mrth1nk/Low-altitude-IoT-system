from __future__ import annotations

import secrets
import time
import uuid

from shared_protocol.auth import (
    AuthError,
    AuthenticatedStreamDecoder,
    PersistentAuthSession,
)
from shared_protocol.frame import (
    Frame,
    FrameError,
    MessageType,
    decode_frame,
    encode_frame,
)

from .inbox import InboxRejected
from .optical_gate import OpticalBlocked


class AircraftLinkServer:
    """ELF serial-stream endpoint for authenticated LIOT frames."""

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
        challenge = secrets.token_bytes(32) if challenge is None else challenge
        if not isinstance(challenge, bytes) or len(challenge) != 32:
            raise ValueError("challenge must contain 32 bytes")
        self.challenge = challenge.hex()
        self.peer_session_nonce = None
        self.sequence = 0
        self.metrics = {
            "received": 0,
            "rejected_auth": 0,
            "rejected_frame": 0,
            "rejected_size": 0,
            "rejected_queue": 0,
        }

    def challenge_datagram(self):
        return self._seal(
            self._frame(
                MessageType.AUTH_CHALLENGE,
                uuid.UUID(int=0),
                {"challenge": self.challenge},
            )
        )

    def feed_bytes(self, data):
        before = self.decoder.rejected_size
        envelopes = self.decoder.feed(data)
        self.metrics["rejected_size"] += self.decoder.rejected_size - before
        responses = []
        for envelope in envelopes:
            responses.extend(self._handle_envelope(envelope))
            if len(responses) >= self.max_responses:
                return responses[: self.max_responses]
        return responses

    def pump_once(self, read_size=4096, snapshot=None):
        if self.stream is None:
            raise RuntimeError("serial byte stream is not configured")
        data = self.stream.read(int(read_size))
        responses = self.feed_bytes(data or b"")
        responses.extend(self.poll_status(snapshot))
        for response in responses:
            self.stream.write(response)
        return len(responses)

    def poll_status(self, snapshot=None):
        frame = self.optical_gate.due_frame(
            snapshot, now=float(self.clock())
        )
        return [] if frame is None else [self._seal(frame)]

    def _handle_envelope(self, envelope):
        try:
            payload, metadata = self.auth.open(envelope)
        except AuthError:
            self.metrics["rejected_auth"] += 1
            return []
        self.metrics["received"] += 1
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

        if nonce != self.peer_session_nonce:
            self.peer_session_nonce = None
            return [self.challenge_datagram()]

        if frame.message_type not in self.optical_gate.CLOUD_COMMAND_TYPES:
            return []
        try:
            self.optical_gate.require_locked(frame)
        except OpticalBlocked:
            return [self._seal(self.optical_gate.blocked_frame())]

        try:
            result = self.inbox.accept(frame)
        except InboxRejected as exc:
            self.metrics["rejected_queue"] += 1
            reason = exc.reason
            if exc.missing:
                reason += ":" + ",".join(str(item) for item in exc.missing)
            return [
                self._seal(
                    self._frame(
                        MessageType.NACK,
                        frame.command_id,
                        {
                            "acked_sequence": frame.sequence,
                            "reason": reason[:256],
                        },
                    )
                )
            ]

        replies = [
            self._seal(
                self._frame(
                    MessageType.ACK,
                    frame.command_id,
                    {"acked_sequence": result.acked_sequence},
                )
            )
        ]
        if result.stage == "MISSION_STAGED":
            replies.append(
                self._seal(
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
