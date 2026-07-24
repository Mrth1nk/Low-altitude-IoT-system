"""Wi-Fi telemetry link adapters for Station Ctrl+Shift+W MAVLink mode."""

from __future__ import annotations

import socket
from dataclasses import dataclass


def parse_udp_target(target: str) -> tuple[str, int]:
    if not target.startswith("udp:"):
        raise ValueError("target must use udp:host:port")
    host, port = target.split("udp:", 1)[1].rsplit(":", 1)
    return host, int(port)


@dataclass
class WiFiTelemetryConfig:
    mode: str = "udp"
    serial_port: str = "/dev/ttyUSB0"
    serial_baud: int = 57600
    udp_host: str = "0.0.0.0"
    udp_port: int = 14560
    udp_target_host: str = "192.168.2.255"
    udp_target_port: int = 14560
    tcp_host: str = "0.0.0.0"
    tcp_port: int = 14560
    timeout: float = 0.02


class UDPWiFiTelemetryLink:
    def __init__(self, config: WiFiTelemetryConfig) -> None:
        self.config = config
        self.sock: socket.socket | None = None
        self.peer = (config.udp_target_host, config.udp_target_port)

    def open(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((self.config.udp_host, self.config.udp_port))
        self.sock.setblocking(False)

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None

    def read(self, size: int = 1024) -> bytes:
        if not self.sock:
            return b""
        try:
            data, addr = self.sock.recvfrom(size)
        except BlockingIOError:
            return b""
        if data:
            self.peer = addr
        return data

    def write(self, data: bytes) -> int:
        if not self.sock or not data:
            return 0
        return self.sock.sendto(data, self.peer)


class SerialWiFiTelemetryLink:
    def __init__(self, config: WiFiTelemetryConfig) -> None:
        self.config = config
        self.port = None

    def open(self) -> None:
        import serial  # type: ignore

        self.port = serial.Serial(self.config.serial_port, self.config.serial_baud, timeout=self.config.timeout, write_timeout=0)

    def close(self) -> None:
        if self.port:
            self.port.close()
            self.port = None

    def read(self, size: int = 1024) -> bytes:
        if not self.port:
            return b""
        waiting = getattr(self.port, "in_waiting", 0) or size
        return self.port.read(min(size, waiting))

    def write(self, data: bytes) -> int:
        if not self.port or not data:
            return 0
        return int(self.port.write(data))


def create_wifi_link(config: WiFiTelemetryConfig):
    mode = config.mode.lower()
    if mode == "udp":
        return UDPWiFiTelemetryLink(config)
    if mode == "serial":
        return SerialWiFiTelemetryLink(config)
    raise ValueError(f"unsupported wifi telemetry mode: {config.mode}")
