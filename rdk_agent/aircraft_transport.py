"""Non-blocking UDP runtime transport for the reliable aircraft link."""

import socket

from shared_protocol.frame import FrameError, MessageType, decode_frame


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
        socket_factory=socket.socket,
    ):
        self.aircraft_link = aircraft_link
        self._peer = tuple(peer) if peer is not None else None
        self._allowed_peer_host = self._peer[0] if self._peer else None
        self._optical_state = "blocked"
        self._link_detail = "awaiting_status"
        self._transport_error = ""
        self._socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((str(local_host), int(local_port)))
        self._socket.setblocking(False)

    @property
    def local_address(self):
        return self._socket.getsockname()

    @property
    def peer(self):
        return self._peer

    def close(self):
        self._socket.close()

    def optical_state(self):
        return self._optical_state

    def status(self):
        return {
            "optical_state": self._optical_state,
            "link_detail": self._link_detail,
            "peer": (
                f"{self._peer[0]}:{self._peer[1]}" if self._peer else ""
            ),
            "transport_error": self._transport_error,
        }

    def transaction_state(self):
        state = self.aircraft_link.transaction_state()
        if self._transport_error:
            state = dict(state)
            state["stage"] = "transport_error"
            state["error"] = self._transport_error[:120]
        return state

    def pump(self, now, max_receive=32):
        self._receive(max_receive)
        if self._peer is None:
            return self.transaction_state()
        try:
            for payload in self.aircraft_link.due_bytes(now):
                self._socket.sendto(payload, self._peer)
            self._transport_error = ""
        except OSError as exc:
            self._transport_error = f"udp send failed: {exc}"
        return self.transaction_state()

    def _receive(self, max_receive):
        for _ in range(max_receive):
            try:
                data, remote = self._socket.recvfrom(65535)
            except BlockingIOError:
                return
            except OSError as exc:
                self._transport_error = f"udp receive failed: {exc}"
                return
            if self._allowed_peer_host and remote[0] != self._allowed_peer_host:
                continue
            try:
                frame = decode_frame(data)
            except FrameError:
                continue
            self._peer = remote
            if self._allowed_peer_host is None:
                self._allowed_peer_host = remote[0]
            if frame.message_type in (MessageType.ACK, MessageType.NACK):
                self.aircraft_link.accept_response(frame)
            elif frame.message_type is MessageType.LINK_BLOCKED:
                self._optical_state = "blocked"
                self._link_detail = str(frame.payload["reason"])[:80]
            elif frame.message_type is MessageType.STATUS:
                state = str(frame.payload["state"]).strip().lower()
                self._optical_state = (
                    "locked" if state in ("locked", "ready", "online") else "blocked"
                )
                self._link_detail = str(
                    frame.payload.get("detail", frame.payload["state"])
                )[:80]
