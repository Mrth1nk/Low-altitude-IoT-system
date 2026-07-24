"""Non-blocking UDP runtime transport for the reliable aircraft link."""

import socket
import time
import uuid

from shared_protocol.auth import (
    AuthError,
    AuthenticatedDatagramCodec,
    PersistentAuthSession,
)
from shared_protocol.frame import (
    Frame,
    FrameError,
    MessageType,
    decode_frame,
    encode_frame,
)


def legacy_gateway_ports(config, transport_port):
    raw = config.get("aircraft_udp_ports", [14550])
    if isinstance(raw, str):
        ports = [int(part.strip()) for part in raw.split(",") if part.strip()]
    else:
        ports = [int(port) for port in raw]
    if int(transport_port) in ports:
        raise ValueError("legacy MAVLink gateway must not share aircraft transport port")
    return ports


class AircraftTransport:
    def __init__(
        self,
        aircraft_link,
        local_host="0.0.0.0",
        local_port=14560,
        peer=("192.168.4.1", 14555),
        psk=None,
        socket_factory=socket.socket,
        clock=time.monotonic,
        status_timeout=3.0,
        retry_backoff=0.25,
        max_retry_backoff=4.0,
        auth_store=None,
    ):
        if peer is None:
            raise ValueError("an exact aircraft peer is required")
        if psk is None or (isinstance(psk, str) and not psk):
            raise ValueError("AIRCRAFT_LINK_PSK is required")
        if status_timeout <= 0:
            raise ValueError("status_timeout must be positive")
        if retry_backoff <= 0 or max_retry_backoff < retry_backoff:
            raise ValueError("invalid retry backoff")
        self.aircraft_link = aircraft_link
        self._peer = (str(peer[0]), int(peer[1]))
        self._auth = (
            PersistentAuthSession(psk, auth_store)
            if auth_store is not None
            else AuthenticatedDatagramCodec(psk)
        )
        self._persistent_auth = auth_store is not None
        self._local_host = str(local_host)
        self._local_port = int(local_port)
        self._socket_factory = socket_factory
        self._clock = clock
        self._status_timeout = float(status_timeout)
        self._retry_backoff = float(retry_backoff)
        self._max_retry_backoff = float(max_retry_backoff)
        self._optical_state = "blocked"
        self._link_detail = "awaiting_status"
        self._last_locked_at = None
        self._transport_error = ""
        self._transport_error_transaction = ""
        self._failure_count = 0
        self._next_reopen_at = 0.0
        self._transport_revision = 0
        self._socket = self._create_socket()

    @property
    def local_address(self):
        if self._socket is None:
            return (self._local_host, self._local_port)
        return self._socket.getsockname()

    @property
    def peer(self):
        return self._peer

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def optical_state(self):
        self._expire_status(self._clock())
        return self._optical_state

    def status(self):
        return {
            "optical_state": self._optical_state,
            "link_detail": self._link_detail,
            "peer": (
                f"{self._peer[0]}:{self._peer[1]}" if self._peer else ""
            ),
            "transport_error": self._transport_error,
            "locked_age": (
                round(max(0.0, self._clock() - self._last_locked_at), 3)
                if self._last_locked_at is not None
                else None
            ),
        }

    def transaction_state(self):
        state = self.aircraft_link.transaction_state()
        if self._transport_error:
            state = dict(state)
            state["stage"] = "transport_retry"
            state["error"] = self._transport_error[:120]
            state["error_transaction_id"] = self._transport_error_transaction
        state["revision"] = (
            f"{state.get('revision', 0)}:{self._transport_revision}"
        )
        return state

    def pump(self, now, max_receive=32):
        now = float(now)
        if not self._ensure_socket(now):
            return self.transaction_state()
        self._receive(max_receive, now)
        self._expire_status(now)
        if self._socket is None:
            return self.transaction_state()
        if self._optical_state != "locked":
            self.aircraft_link.fail_transaction(
                "link_blocked", self._link_detail or "optical link blocked"
            )
            return self.transaction_state()
        try:
            for payload in self.aircraft_link.due_bytes(now):
                self._socket.sendto(self._seal(payload), self._peer)
        except OSError as exc:
            self._schedule_reopen(now, f"udp send failed: {exc}")
        return self.transaction_state()

    def _receive(self, max_receive, now):
        for _ in range(max_receive):
            try:
                data, remote = self._socket.recvfrom(65535)
            except BlockingIOError:
                return
            except OSError as exc:
                self._schedule_reopen(now, f"udp receive failed: {exc}")
                return
            if (str(remote[0]), int(remote[1])) != self._peer:
                continue
            try:
                data = self._open(data)
            except AuthError:
                continue
            try:
                frame = decode_frame(data)
            except FrameError:
                continue
            if frame.message_type is MessageType.AUTH_CHALLENGE:
                response = Frame(
                    MessageType.AUTH_RESPONSE,
                    0,
                    frame.sequence,
                    uuid.UUID(int=0),
                    {"challenge": frame.payload["challenge"]},
                )
                try:
                    self._socket.sendto(
                        self._seal(encode_frame(response)), self._peer
                    )
                except OSError as exc:
                    self._schedule_reopen(
                        now, f"challenge response failed: {exc}"
                    )
                    return
            elif frame.message_type in (MessageType.ACK, MessageType.NACK):
                self.aircraft_link.accept_response(frame)
            elif frame.message_type is MessageType.LINK_BLOCKED:
                self._block(str(frame.payload["reason"])[:80])
            elif frame.message_type is MessageType.STATUS:
                state = str(frame.payload["state"]).strip().lower()
                detail = str(
                    frame.payload.get("detail", frame.payload["state"])
                )[:80]
                if state in ("locked", "ready", "online"):
                    self._optical_state = "locked"
                    self._link_detail = detail
                    self._last_locked_at = now
                else:
                    self._block(detail)

    def _block(self, detail):
        self._optical_state = "blocked"
        self._link_detail = detail or "optical link blocked"
        self.aircraft_link.fail_transaction(
            "link_blocked", self._link_detail
        )

    def _seal(self, payload):
        return self._auth.seal(payload)

    def _open(self, datagram):
        opened = self._auth.open(datagram)
        return opened[0] if self._persistent_auth else opened

    def _expire_status(self, now):
        if (
            self._optical_state == "locked"
            and self._last_locked_at is not None
            and float(now) - self._last_locked_at > self._status_timeout
        ):
            self._block("status_timeout")

    def _create_socket(self):
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self._local_host, self._local_port))
        sock.setblocking(False)
        return sock

    def _ensure_socket(self, now):
        if self._socket is not None:
            return True
        if now < self._next_reopen_at:
            return False
        try:
            self._socket = self._create_socket()
        except OSError as exc:
            self._schedule_reopen(now, f"udp reopen failed: {exc}")
            return False
        self._failure_count = 0
        self._transport_error = ""
        self._transport_error_transaction = ""
        self._transport_revision += 1
        return True

    def _schedule_reopen(self, now, error):
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._failure_count += 1
        delay = min(
            self._max_retry_backoff,
            self._retry_backoff * (2 ** (self._failure_count - 1)),
        )
        self._next_reopen_at = float(now) + delay
        self._transport_error = error
        self._transport_revision += 1
        active_command_id = self.aircraft_link.active_command_id
        self._transport_error_transaction = (
            str(active_command_id) if active_command_id is not None else ""
        )
