#!/usr/bin/env python3
"""Self tests for the OnboardBridge MAVLink telemetry bridge."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from device_finder import list_serial_devices
from mavlink_monitor import MAVLinkMonitor
from optical_link_state import OpticalLinkStateReader, write_optical_link_state
from serial_bridge import (
    SIGNAL_INTERRUPTED,
    SIGNAL_RESTORED,
    SerialBridge,
    build_statustext_packet,
    is_mission_downlink_packet,
)
from wifi_telemetry_link import parse_udp_target


ROOT = Path(__file__).resolve().parent
STATION = ROOT.parent / "Station"


def build_v2_heartbeat() -> bytes:
    payload = bytearray(9)
    payload[0:4] = (5).to_bytes(4, "little")
    payload[4] = 2
    payload[5] = 3
    payload[6] = 0x80
    payload[7] = 4
    payload[8] = 3
    header = bytes([0xFD, len(payload), 0, 0, 7, 1, 1, 0, 0, 0])
    return header + bytes(payload) + b"\x00\x00"


class StationProtocolTests(unittest.TestCase):
    def test_station_path_exists(self) -> None:
        if not STATION.exists():
            self.skipTest(f"Station source is not deployed on this board: {STATION}")
        self.assertTrue(STATION.exists(), STATION)

    def test_ctrl_shift_w_mode_uses_udp_mavlink_14560(self) -> None:
        if not STATION.exists():
            self.skipTest(f"Station source is not deployed on this board: {STATION}")
        app = (STATION / "src" / "renderer" / "App.tsx").read_text(encoding="utf-8")
        service = (STATION / "src" / "main" / "mavlinkWifiService.ts").read_text(encoding="utf-8")
        protocol = (STATION / "src" / "main" / "mavlinkProtocol.ts").read_text(encoding="utf-8")
        self.assertIn("event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'w'", app)
        self.assertIn("dgram.createSocket('udp4')", service)
        self.assertIn("socket.bind(port, '0.0.0.0'", service)
        self.assertIn("MAVLINK_WIFI_PORT = 14560", protocol)
        self.assertIn("new MavlinkPacketParser()", service)


class ModuleTests(unittest.TestCase):
    def test_modules_import(self) -> None:
        self.assertTrue(SerialBridge)
        self.assertEqual(parse_udp_target("udp:127.0.0.1:14560"), ("127.0.0.1", 14560))

    def test_device_finder_returns_list(self) -> None:
        self.assertIsInstance(list_serial_devices(), list)

    def test_monitor_parses_heartbeat_without_pymavlink(self) -> None:
        monitor = MAVLinkMonitor()
        events = monitor.feed(build_v2_heartbeat())
        self.assertEqual(events[-1]["type"], "HEARTBEAT")
        self.assertEqual(events[-1]["system_id"], 1)
        self.assertEqual(events[-1]["component_id"], 1)
        self.assertEqual(events[-1]["mode"], "LOITER")
        self.assertTrue(events[-1]["armed"])

    def test_config_loads(self) -> None:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["wifi_telemetry"]["mode"], "serial")
        self.assertEqual(config["wifi_telemetry"]["port"], "/dev/ttyUSB0")
        self.assertEqual(config["wifi_telemetry"]["udp_target_port"], 14560)

    def test_simulated_bridge_can_start_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = SerialBridge(simulate=True, log_path=str(Path(tmp) / "bridge.log"))
            bridge.start()
            bridge.stop()
            self.assertGreaterEqual(bridge.get_stats()["uptime_sec"], 0)

    def test_optical_link_status_blocks_bridge_downlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "optical.json"
            write_optical_link_state(str(status_file), blocked=True, state="LOST", detected=False, lost_frames=20)
            reader = OpticalLinkStateReader(str(status_file), stale_after_sec=2.0)
            self.assertTrue(reader.read().blocked)

            bridge = SerialBridge(simulate=True, optical_status_file=str(status_file), log_path=str(Path(tmp) / "bridge.log"))
            self.assertTrue(bridge._optical_link_blocked())

            write_optical_link_state(str(status_file), blocked=False, state="TRACK", detected=True, lost_frames=0)
            self.assertFalse(bridge._optical_link_blocked())

    def test_optical_link_status_fails_closed_when_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "optical.json"
            reader = OpticalLinkStateReader(str(status_file), stale_after_sec=2.0)

            missing = reader.read()
            self.assertTrue(missing.blocked)
            self.assertEqual(missing.reason, "missing")

            status_file.write_text("not-json", encoding="utf-8")
            invalid = reader.read()
            self.assertTrue(invalid.blocked)
            self.assertTrue(invalid.reason.startswith("invalid:"))

            status_file.write_text(
                json.dumps({
                    "timestamp": time.time() - 10.0,
                    "link_blocked": False,
                    "state": "TRACK",
                }),
                encoding="utf-8",
            )
            stale = reader.read()
            self.assertTrue(stale.blocked)
            self.assertTrue(stale.stale)

    def test_optical_link_status_sends_statustext_on_state_changes(self) -> None:
        class FakeWifi:
            def __init__(self) -> None:
                self.writes = []

            def write(self, payload: bytes) -> int:
                self.writes.append(payload)
                return len(payload)

        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "optical.json"
            bridge = SerialBridge(
                simulate=True,
                optical_status_file=str(status_file),
                log_path=str(Path(tmp) / "bridge.log"),
            )
            fake = FakeWifi()
            bridge.wifi = fake

            write_optical_link_state(str(status_file), blocked=True, state="SEARCH", detected=False, lost_frames=1)
            self.assertTrue(bridge._optical_link_blocked())
            self.assertEqual(len(fake.writes), 1)
            self.assertEqual(fake.writes[0][0], 0xFD)
            self.assertIn(SIGNAL_INTERRUPTED.encode(), fake.writes[0])
            bridge._last_link_status_send = 0.0
            bridge._flush_link_status()
            self.assertEqual(len(fake.writes), 2)

            write_optical_link_state(str(status_file), blocked=False, state="TRACK", detected=True, lost_frames=0)
            self.assertFalse(bridge._optical_link_blocked())
            self.assertEqual(len(fake.writes), 3)
            self.assertIn(SIGNAL_RESTORED.encode(), fake.writes[2])

    def test_optical_link_status_blocks_station_uplink(self) -> None:
        class FakeFc:
            def __init__(self) -> None:
                self.writes = []

            def write(self, payload: bytes) -> int:
                self.writes.append(payload)
                return len(payload)

        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "optical.json"
            write_optical_link_state(str(status_file), blocked=True, state="LOST", detected=False, lost_frames=20)
            bridge = SerialBridge(
                simulate=True,
                optical_status_file=str(status_file),
                log_path=str(Path(tmp) / "bridge.log"),
            )
            fake_fc = FakeFc()
            bridge.fc = fake_fc

            self.assertTrue(bridge._optical_link_blocked())
            self.assertFalse(bridge._write_wifi_to_fc(b"\xfdcommand"))
            self.assertEqual(fake_fc.writes, [])
            self.assertEqual(bridge.stats.optical_blocked_bytes, len(b"\xfdcommand"))

            write_optical_link_state(str(status_file), blocked=False, state="TRACK", detected=True, lost_frames=0)
            self.assertFalse(bridge._optical_link_blocked())
            self.assertTrue(bridge._write_wifi_to_fc(b"\xfdcommand"))
            self.assertEqual(fake_fc.writes, [b"\xfdcommand"])

    def test_blocked_status_repeats_for_late_station_connection(self) -> None:
        class FakeWifi:
            def __init__(self) -> None:
                self.writes = []

            def write(self, payload: bytes) -> int:
                self.writes.append(payload)
                return len(payload)

        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "optical.json"
            write_optical_link_state(str(status_file), blocked=True, state="STARTING", detected=False, lost_frames=0)
            bridge = SerialBridge(
                simulate=True,
                optical_status_file=str(status_file),
                log_path=str(Path(tmp) / "bridge.log"),
            )
            fake = FakeWifi()
            bridge.wifi = fake

            self.assertTrue(bridge._optical_link_blocked())
            bridge._pending_link_status = None
            bridge._pending_link_status_count = 0
            bridge._last_link_status_send = 0.0
            bridge._flush_link_status()

            self.assertEqual(len(fake.writes), 2)
            self.assertIn(SIGNAL_INTERRUPTED.encode(), fake.writes[-1])

    def test_build_statustext_packet_uses_mavlink2(self) -> None:
        packet = build_statustext_packet(7, SIGNAL_INTERRUPTED)
        self.assertEqual(packet[0], 0xFD)
        self.assertEqual(packet[1], 54)
        self.assertEqual(packet[7], 253)
        self.assertIn(SIGNAL_INTERRUPTED.encode(), packet)

    def test_monitor_parses_mission_upload_messages(self) -> None:
        monitor = MAVLinkMonitor()
        count_payload = b"\x02\x00\x01\x01\x00"
        ack_payload = b"\xff\xbe\x00\x00"

        count_packet = self._mavlink2_packet(44, count_payload)
        ack_packet = self._mavlink2_packet(47, ack_payload)
        events = monitor.feed(count_packet + ack_packet)

        self.assertEqual(events[0]["type"], "MISSION_COUNT")
        self.assertEqual(events[0]["count"], 2)
        self.assertEqual(events[1]["type"], "MISSION_ACK")
        self.assertEqual(events[1]["result"], 0)

    def test_optical_block_allows_mission_downlink_handshake(self) -> None:
        request_payload = b"\x00\x00\xff\xbe\x00"
        ack_payload = b"\xff\xbe\x00\x00"

        request_packet = self._mavlink2_packet(51, request_payload)
        ack_packet = self._mavlink2_packet(47, ack_payload)

        self.assertTrue(is_mission_downlink_packet(request_packet))
        self.assertTrue(is_mission_downlink_packet(ack_packet))
        self.assertFalse(is_mission_downlink_packet(build_v2_heartbeat()))

    @staticmethod
    def _mavlink2_packet(msg_id: int, payload: bytes) -> bytes:
        return bytes([
            0xFD,
            len(payload),
            0,
            0,
            0,
            255,
            190,
            msg_id & 0xFF,
            (msg_id >> 8) & 0xFF,
            (msg_id >> 16) & 0xFF,
        ]) + payload + b"\x00\x00"


if __name__ == "__main__":
    unittest.main(verbosity=2)
