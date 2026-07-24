"""Authenticated datagram envelope for LIOT frames."""

from collections import OrderedDict
import hashlib
import hmac
import os
import struct


MAGIC = b"LIA1"
HEADER = struct.Struct(">4s16sQI")
MAC_SIZE = hashlib.sha256().digest_size
MAX_PAYLOAD_SIZE = 1024 * 1024


class AuthError(ValueError):
    """Raised when a datagram is malformed or fails authentication."""


class ReplayError(AuthError):
    """Raised when an authenticated datagram is outside the replay window."""


class AuthenticatedDatagramCodec:
    def __init__(
        self,
        psk,
        session_nonce=None,
        replay_window=64,
        max_sessions=8,
    ):
        if isinstance(psk, str):
            psk = psk.encode("utf-8")
        if not isinstance(psk, bytes) or len(psk) < 16:
            raise ValueError("PSK must contain at least 16 bytes")
        if session_nonce is None:
            session_nonce = os.urandom(16)
        if not isinstance(session_nonce, bytes) or len(session_nonce) != 16:
            raise ValueError("session nonce must contain exactly 16 bytes")
        if not 1 <= int(replay_window) <= 4096:
            raise ValueError("replay_window must be between 1 and 4096")
        if int(max_sessions) < 1:
            raise ValueError("max_sessions must be positive")
        self._psk = psk
        self._nonce = session_nonce
        self._counter = 0
        self._window = int(replay_window)
        self._window_mask = (1 << self._window) - 1
        self._max_sessions = int(max_sessions)
        self._sessions = OrderedDict()

    @property
    def session_count(self):
        return len(self._sessions)

    def seal(self, payload):
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("payload must be bytes-like")
        payload = bytes(payload)
        if len(payload) > MAX_PAYLOAD_SIZE:
            raise ValueError("authenticated payload exceeds limit")
        counter = self._counter
        self._counter += 1
        header = HEADER.pack(MAGIC, self._nonce, counter, len(payload))
        body = header + payload
        return body + hmac.new(self._psk, body, hashlib.sha256).digest()

    def open(self, datagram):
        if not isinstance(datagram, (bytes, bytearray, memoryview)):
            raise TypeError("datagram must be bytes-like")
        datagram = bytes(datagram)
        if len(datagram) < HEADER.size + MAC_SIZE:
            raise AuthError("authenticated datagram is truncated")
        magic, nonce, counter, payload_length = HEADER.unpack_from(datagram)
        if magic != MAGIC:
            raise AuthError("bad authenticated datagram magic")
        if payload_length > MAX_PAYLOAD_SIZE:
            raise AuthError("authenticated payload exceeds limit")
        expected_length = HEADER.size + payload_length + MAC_SIZE
        if len(datagram) != expected_length:
            raise AuthError("authenticated datagram length mismatch")
        body = datagram[:-MAC_SIZE]
        supplied_mac = datagram[-MAC_SIZE:]
        expected_mac = hmac.new(self._psk, body, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_mac, expected_mac):
            raise AuthError("authenticated datagram MAC mismatch")
        self._accept_counter(nonce, counter)
        return datagram[HEADER.size:-MAC_SIZE]

    def _accept_counter(self, nonce, counter):
        state = self._sessions.get(nonce)
        if state is None:
            self._sessions[nonce] = (counter, 1)
            self._sessions.move_to_end(nonce)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            return

        highest, bitmap = state
        if counter > highest:
            shift = counter - highest
            bitmap = (
                1 if shift >= self._window
                else ((bitmap << shift) | 1) & self._window_mask
            )
            highest = counter
        else:
            distance = highest - counter
            if distance >= self._window:
                raise ReplayError("authenticated datagram is outside replay window")
            bit = 1 << distance
            if bitmap & bit:
                raise ReplayError("authenticated datagram was already received")
            bitmap |= bit
        self._sessions[nonce] = (highest, bitmap)
        self._sessions.move_to_end(nonce)
