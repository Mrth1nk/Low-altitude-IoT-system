"""MAVLink transparent forwarding between flight controller serial and telemetry link."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from logger import setup_logger
from mavlink_monitor import MAVLinkMonitor
from optical_link_state import DEFAULT_STATUS_FILE, OpticalLinkStateReader
from wifi_telemetry_link import WiFiTelemetryConfig, create_wifi_link

MAVLINK_V2_MAGIC = 0xFD
MAVLINK_STATUSTEXT = 253
MAVLINK_STATUSTEXT_CRC_EXTRA = 83
SIGNAL_INTERRUPTED = "SIGNAL_INTERRUPTED:OPTICAL_LINK_BLOCKED"
SIGNAL_RESTORED = "SIGNAL_RESTORED:OPTICAL_LINK_CLEAR"
BLOCKED_STATUS_REPEAT_SEC = 0.5
MISSION_UPLOAD_MSG_IDS = {44, 45, 73}
MISSION_HOME_MSG_IDS = {48, 75}
MISSION_DOWNLINK_MSG_IDS = {40, 42, 47, 51}
MAVLINK_COMMAND_LONG = 76
MAV_CMD_DO_SET_HOME = 179


@dataclass
class BridgeStats:
    fc_to_wifi_bytes: int = 0
    wifi_to_fc_bytes: int = 0
    reconnects_fc: int = 0
    reconnects_wifi: int = 0
    optical_blocked_bytes: int = 0
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "fc_to_wifi_bytes": self.fc_to_wifi_bytes,
            "wifi_to_fc_bytes": self.wifi_to_fc_bytes,
            "reconnects_fc": self.reconnects_fc,
            "reconnects_wifi": self.reconnects_wifi,
            "optical_blocked_bytes": self.optical_blocked_bytes,
            "uptime_sec": round(time.time() - self.started_at, 3),
        }


class SerialBridge:
    def __init__(
        self,
        fc_port: str = "/dev/ttyACM0",
        fc_baud: int = 57600,
        wifi_port: str = "/dev/ttyUSB0",
        wifi_baud: int = 57600,
        wifi_mode: str = "udp",
        udp_host: str = "0.0.0.0",
        udp_port: int = 14560,
        udp_target_host: str = "192.168.2.255",
        udp_target_port: int = 14560,
        read_chunk_size: int = 1024,
        serial_timeout: float = 0.02,
        reconnect_interval: float = 2.0,
        stats_interval: float = 2.0,
        optical_status_file: str | None = DEFAULT_STATUS_FILE,
        optical_status_stale_sec: float = 2.0,
        log_path: str | None = None,
        simulate: bool = False,
        log=None,
    ) -> None:
        self.fc_port_name = fc_port
        self.fc_baud = int(fc_baud)
        self.read_chunk_size = int(read_chunk_size)
        self.serial_timeout = float(serial_timeout)
        self.reconnect_interval = float(reconnect_interval)
        self.stats_interval = float(stats_interval)
        self.simulate = simulate
        self.log = log or setup_logger(log_path=log_path)
        self.fc = None
        self.running = False
        self.monitor = MAVLinkMonitor()
        self.wifi_monitor = MAVLinkMonitor()
        self._last_heartbeat_log = 0.0
        self._optical_blocked = False
        self._link_status_seq = 0
        self._pending_link_status: str | None = None
        self._pending_link_status_count = 0
        self._last_link_status_send = 0.0
        self.stats = BridgeStats()
        self.optical_reader = OpticalLinkStateReader(optical_status_file, optical_status_stale_sec) if optical_status_file else None
        self.wifi_config = WiFiTelemetryConfig(
            mode=wifi_mode,
            serial_port=wifi_port,
            serial_baud=int(wifi_baud),
            udp_host=udp_host,
            udp_port=int(udp_port),
            udp_target_host=udp_target_host,
            udp_target_port=int(udp_target_port),
            timeout=self.serial_timeout,
        )
        self.wifi = create_wifi_link(self.wifi_config)

    def start(self) -> None:
        self.running = True
        self.log.info("[OnboardBridge] Starting WiFi telemetry bridge")
        if self.simulate:
            self.log.info("[OnboardBridge] Simulate mode enabled")
            return
        self.reconnect_fc()
        self.reconnect_wifi()
        self.log.info("[Bridge] FC -> WiFi forwarding started")
        self.log.info("[Bridge] WiFi -> FC forwarding started")
        self.log.info("[MAVLink] Waiting for heartbeat...")

    def stop(self) -> None:
        self.running = False
        if self.fc:
            self.fc.close()
            self.fc = None
        self.wifi.close()

    def loop(self) -> None:
        self.start()
        next_stats = time.time() + self.stats_interval
        next_heartbeat_notice = time.time() + self.stats_interval
        try:
            while self.running:
                self._forward_once()
                now = time.time()
                if now >= next_stats:
                    self.log.info("[Bridge] stats %s", self.get_stats())
                    next_stats = now + self.stats_interval
                if not self.monitor.last_heartbeat and now >= next_heartbeat_notice:
                    self.log.warning("[MAVLink] No heartbeat yet. Check FC port, baudrate, and cable.")
                    next_heartbeat_notice = now + self.stats_interval
                time.sleep(0.001)
        finally:
            self.stop()

    def reconnect_fc(self) -> None:
        if self.simulate:
            return
        while self.running and self.fc is None:
            try:
                import serial  # type: ignore

                self.fc = serial.Serial(self.fc_port_name, self.fc_baud, timeout=self.serial_timeout, write_timeout=0)
                self.log.info("[FC] Port: %s, baud: %s", self.fc_port_name, self.fc_baud)
            except Exception as exc:
                self.stats.reconnects_fc += 1
                self.log.error("[FC] disconnected, trying to reconnect... %s", exc)
                time.sleep(self.reconnect_interval)

    def reconnect_wifi(self) -> None:
        if self.simulate:
            return
        while self.running:
            try:
                self.wifi.open()
                if self.wifi_config.mode == "udp":
                    self.log.info(
                        "[WiFiTelemetry] UDP bind %s:%s target %s:%s",
                        self.wifi_config.udp_host,
                        self.wifi_config.udp_port,
                        self.wifi_config.udp_target_host,
                        self.wifi_config.udp_target_port,
                    )
                else:
                    self.log.info("[WiFiTelemetry] Port: %s, baud: %s", self.wifi_config.serial_port, self.wifi_config.serial_baud)
                return
            except Exception as exc:
                self.stats.reconnects_wifi += 1
                self.log.error("[WiFiTelemetry] disconnected, trying to reconnect... %s", exc)
                time.sleep(self.reconnect_interval)

    def _forward_once(self) -> None:
        if self.simulate:
            return
        if self.fc is None:
            self.reconnect_fc()
            return
        try:
            waiting = getattr(self.fc, "in_waiting", 0) or self.read_chunk_size
            fc_data = self.fc.read(min(self.read_chunk_size, waiting))
            optical_blocked = self._optical_link_blocked()
            self._flush_link_status()
            if fc_data:
                mission_downlink = is_mission_downlink_packet(fc_data)
                if optical_blocked and not mission_downlink:
                    self.stats.optical_blocked_bytes += len(fc_data)
                else:
                    if optical_blocked and mission_downlink:
                        self.log.info("[OpticalLink] allowing mission handshake from FC while optical link is blocked (%s bytes)", len(fc_data))
                    self.wifi.write(fc_data)
                    self.stats.fc_to_wifi_bytes += len(fc_data)
                for event in self.monitor.feed(fc_data):
                    now = time.time()
                    if event["type"] == "HEARTBEAT" and now - self._last_heartbeat_log >= 1.0:
                        self._last_heartbeat_log = now
                        self.log.info(
                            "[MAVLink] Heartbeat received: system=%s component=%s mode=%s armed=%s",
                            event["system_id"],
                            event["component_id"],
                            event["mode"],
                            event["armed"],
                        )
                    elif event["type"] in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                        self.log.info(
                            "[FC->WiFi] %s seq=%s target=%s/%s mission_type=%s",
                            event["type"],
                            event["seq"],
                            event["target_system"],
                            event["target_component"],
                            event["mission_type"],
                        )
                    elif event["type"] == "MISSION_ACK":
                        self.log.info(
                            "[FC->WiFi] MISSION_ACK result=%s target=%s/%s mission_type=%s",
                            event["result"],
                            event["target_system"],
                            event["target_component"],
                            event["mission_type"],
                        )
                    elif event["type"] == "MISSION_CURRENT":
                        self.log.info("[FC->WiFi] MISSION_CURRENT seq=%s", event["seq"])

            wifi_data = self.wifi.read(self.read_chunk_size)
            if wifi_data:
                if not self._write_wifi_to_fc(wifi_data):
                    return
                if len(wifi_data) != 19:
                    self.log.info("[WiFi->FC] raw %s", wifi_data.hex(" ").upper())
                for event in self.wifi_monitor.feed(wifi_data):
                    if event["type"] == "SET_MODE":
                        self.log.info(
                            "[WiFi->FC] SET_MODE from system=%s component=%s target_system=%s mode=%s custom_mode=%s",
                            event["system_id"],
                            event["component_id"],
                            event["target_system"],
                            event["mode"],
                            event["custom_mode"],
                        )
                    elif event["type"] == "COMMAND_LONG":
                        self.log.info(
                            "[WiFi->FC] COMMAND_LONG from system=%s component=%s target=%s/%s command=%s param1=%.3f",
                            event["system_id"],
                            event["component_id"],
                            event["target_system"],
                            event["target_component"],
                            event["command"],
                            event["param1"],
                        )
                    elif event["type"] == "COMMAND_ACK":
                        self.log.info(
                            "[WiFi->FC] COMMAND_ACK from system=%s component=%s command=%s result=%s",
                            event["system_id"],
                            event["component_id"],
                            event["command"],
                            event["result"],
                        )
                    elif event["type"] == "MISSION_CLEAR_ALL":
                        self.log.info(
                            "[WiFi->FC] MISSION_CLEAR_ALL from system=%s component=%s target=%s/%s mission_type=%s",
                            event["system_id"],
                            event["component_id"],
                            event["target_system"],
                            event["target_component"],
                            event["mission_type"],
                        )
                    elif event["type"] == "MISSION_COUNT":
                        self.log.info(
                            "[WiFi->FC] MISSION_COUNT from system=%s component=%s count=%s target=%s/%s mission_type=%s",
                            event["system_id"],
                            event["component_id"],
                            event["count"],
                            event["target_system"],
                            event["target_component"],
                            event["mission_type"],
                        )
                    elif event["type"] == "MISSION_ITEM_INT":
                        self.log.info(
                            "[WiFi->FC] MISSION_ITEM_INT from system=%s component=%s seq=%s command=%s target=%s/%s lat=%.7f lon=%.7f alt=%.1f frame=%s type=%s",
                            event["system_id"],
                            event["component_id"],
                            event["seq"],
                            event["command"],
                            event["target_system"],
                            event["target_component"],
                            event["lat"],
                            event["lon"],
                            event["alt"],
                            event["frame"],
                            event["mission_type"],
                        )
        except Exception as exc:
            self.log.error("[Bridge] forwarding error: %s", exc)
            if self.fc:
                try:
                    self.fc.close()
                except Exception:
                    pass
            self.fc = None
            self.wifi.close()
            self.reconnect_fc()
            self.reconnect_wifi()

    def get_stats(self) -> dict:
        return self.stats.as_dict()

    def _write_wifi_to_fc(self, wifi_data: bytes) -> bool:
        mission_upload = is_mission_upload_or_home_packet(wifi_data)
        if self._optical_blocked and not mission_upload:
            self.stats.optical_blocked_bytes += len(wifi_data)
            self.log.warning("[OpticalLink] blocked station command; WiFi telemetry to FC paused (%s bytes)", len(wifi_data))
            self._flush_link_status()
            return False
        if self._optical_blocked and mission_upload:
            self.log.info("[OpticalLink] allowing mission upload/home packet while optical link is blocked (%s bytes)", len(wifi_data))
        self.fc.write(wifi_data)
        self.stats.wifi_to_fc_bytes += len(wifi_data)
        self.log.info("[WiFi->FC] wrote %s bytes to flight controller", len(wifi_data))
        return True

    def _optical_link_blocked(self) -> bool:
        if not self.optical_reader:
            return False
        state = self.optical_reader.read()
        blocked = state.blocked
        if blocked != self._optical_blocked:
            self._optical_blocked = blocked
            if blocked:
                self.log.warning("[OpticalLink] blocked by tracker state=%s reason=%s; MAVLink bridge paused", state.state, state.reason)
                self._queue_link_status(SIGNAL_INTERRUPTED)
            else:
                self.log.info("[OpticalLink] restored state=%s reason=%s", state.state, state.reason)
                self._queue_link_status(SIGNAL_RESTORED)
        return blocked

    def _queue_link_status(self, text: str) -> None:
        self._pending_link_status = text
        self._pending_link_status_count = 5
        self._last_link_status_send = 0.0
        self._flush_link_status()

    def _flush_link_status(self) -> None:
        now = time.time()
        if not self._pending_link_status or self._pending_link_status_count <= 0:
            if self._optical_blocked and now - self._last_link_status_send >= BLOCKED_STATUS_REPEAT_SEC:
                self._last_link_status_send = now
                self._send_link_status(SIGNAL_INTERRUPTED)
            return
        if now - self._last_link_status_send < 0.05:
            return
        self._last_link_status_send = now
        self._send_link_status(self._pending_link_status)
        self._pending_link_status_count -= 1
        if self._pending_link_status_count <= 0:
            self._pending_link_status = None

    def _send_link_status(self, text: str) -> None:
        try:
            packet = build_statustext_packet(self._link_status_seq, text)
            self._link_status_seq = (self._link_status_seq + 1) & 0xFF
            self.wifi.write(packet)
        except Exception as exc:
            self.log.debug("[OpticalLink] status packet skipped: %s", exc)


def build_statustext_packet(seq: int, text: str, severity: int = 4, system_id: int = 255, component_id: int = 190) -> bytes:
    encoded = text.encode("utf-8", errors="replace")[:50]
    payload = bytes([severity & 0xFF]) + encoded.ljust(50, b"\0") + b"\0\0\0"
    header = bytes(
        [
            MAVLINK_V2_MAGIC,
            len(payload),
            0,
            0,
            seq & 0xFF,
            system_id & 0xFF,
            component_id & 0xFF,
            MAVLINK_STATUSTEXT & 0xFF,
            (MAVLINK_STATUSTEXT >> 8) & 0xFF,
            (MAVLINK_STATUSTEXT >> 16) & 0xFF,
        ]
    )
    checksum = x25_crc(header[1:] + payload + bytes([MAVLINK_STATUSTEXT_CRC_EXTRA]))
    return header + payload + checksum.to_bytes(2, "little")


def is_mission_upload_or_home_packet(data: bytes) -> bool:
    """Allow mission upload bookkeeping through the optical gate.

    This does not allow arming or mode changes. It only lets the ground station
    write a mission, clear a mission, and set a temporary origin/home for indoor
    mission-write testing.
    """
    for msg_id, payload in mavlink_frames(data):
        if msg_id in MISSION_UPLOAD_MSG_IDS or msg_id in MISSION_HOME_MSG_IDS:
            return True
        if msg_id == MAVLINK_COMMAND_LONG and len(payload) >= 30:
            command = int.from_bytes(payload[28:30], "little")
            if command == MAV_CMD_DO_SET_HOME:
                return True
    return False


def is_mission_downlink_packet(data: bytes) -> bool:
    """Allow flight-controller mission handshake replies through the optical gate."""
    return any(msg_id in MISSION_DOWNLINK_MSG_IDS for msg_id, _payload in mavlink_frames(data))


def mavlink_frames(data: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    offset = 0
    while offset <= len(data) - 8:
        magic = data[offset]
        if magic not in (0xFE, 0xFD):
            offset += 1
            continue
        payload_len = data[offset + 1]
        is_v2 = magic == 0xFD
        header_len = 10 if is_v2 else 6
        signature_len = 13 if is_v2 and (data[offset + 2] & 0x01) else 0
        frame_len = header_len + payload_len + 2 + signature_len
        if offset + frame_len > len(data):
            break
        frame = data[offset : offset + frame_len]
        msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16) if is_v2 else frame[5]
        payload = frame[header_len : header_len + payload_len]
        frames.append((msg_id, payload))
        offset += frame_len
    return frames


def x25_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc
