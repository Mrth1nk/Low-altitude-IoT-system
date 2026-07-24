import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aircraft_commands import (
    MAV_CMD_COMPONENT_ARM_DISARM,
    MAV_CMD_DO_SET_MODE,
    MAV_CMD_NAV_WAYPOINT,
    MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
    build_aircraft_mission_packets,
    build_aircraft_command_packets,
    _build_temporary_origin_packets,
    _drain_mission_socket,
    _needs_temporary_origin,
    send_aircraft_command,
)


class FakeSocket:
    instances = []

    def __init__(self, *args):
        self.args = args
        self.options = []
        self.bound = None
        self.sent = []
        self.closed = False
        FakeSocket.instances.append(self)

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, address):
        self.bound = address

    def sendto(self, packet, address):
        self.sent.append((packet, address))
        return len(packet)

    def close(self):
        self.closed = True


def _message_id(packet: bytes) -> int:
    return packet[7] | (packet[8] << 8) | (packet[9] << 16)


def test_mode_command_builds_mavlink2_do_set_mode_and_set_mode_packets():
    packets = build_aircraft_command_packets("aircraft_guided")

    assert [packet[0] for packet in packets] == [0xFD, 0xFD]
    assert [_message_id(packet) for packet in packets] == [76, 11]
    assert int.from_bytes(packets[0][38:40], "little") == MAV_CMD_DO_SET_MODE
    assert packets[0][40] == 1
    assert packets[0][41] == 1
    assert packets[1][10] == 1
    assert int.from_bytes(packets[1][12:16], "little") == 4


def test_arm_command_builds_mavlink2_command_long_packet():
    packets = build_aircraft_command_packets("aircraft_arm")

    assert len(packets) == 1
    assert packets[0][0] == 0xFD
    assert _message_id(packets[0]) == 76
    assert int.from_bytes(packets[0][38:40], "little") == MAV_CMD_COMPONENT_ARM_DISARM
    assert packets[0][40] == 1
    assert packets[0][41] == 1


def test_aircraft_goto_builds_guided_and_global_position_target():
    packets = build_aircraft_command_packets(
        "aircraft_goto",
        target_lat=32.11956,
        target_lng=118.958406,
        target_alt=25,
    )

    assert [_message_id(packet) for packet in packets] == [76, 11, 86]
    payload = packets[2][10:-2]
    assert int.from_bytes(payload[4:8], "little", signed=True) == int(32.11956 * 1e7)
    assert int.from_bytes(payload[8:12], "little", signed=True) == int(118.958406 * 1e7)
    assert round(__import__("struct").unpack_from("<f", payload, 12)[0], 1) == 25.0
    assert int.from_bytes(payload[48:50], "little") == 0b0000111111111000
    assert payload[52] == MAV_FRAME_GLOBAL_RELATIVE_ALT_INT


def test_aircraft_mission_packets_write_auto_waypoint():
    clear_packet, count_packet, item_packets = build_aircraft_mission_packets(
        target_lat=32.11956,
        target_lng=118.958406,
        target_alt=25,
    )
    item_packet = item_packets[0]

    assert [_message_id(packet) for packet in (clear_packet, count_packet, item_packet)] == [45, 44, 73]
    count_payload = count_packet[10:-2]
    assert int.from_bytes(count_payload[0:2], "little") == 1
    assert len(item_packets) == 1
    item_payload = item_packet[10:-2]
    assert int.from_bytes(item_payload[28:30], "little") == 0
    assert int.from_bytes(item_payload[30:32], "little") == MAV_CMD_NAV_WAYPOINT
    assert int.from_bytes(item_payload[16:20], "little", signed=True) == int(32.11956 * 1e7)
    assert int.from_bytes(item_payload[20:24], "little", signed=True) == int(118.958406 * 1e7)
    assert round(__import__("struct").unpack_from("<f", item_payload, 24)[0], 1) == 25.0
    assert item_payload[34] == MAV_FRAME_GLOBAL_RELATIVE_ALT_INT


def test_aircraft_test_home_mission_writes_reference_then_target():
    clear_packet, count_packet, item_packets = build_aircraft_mission_packets(
        target_lat=32.12012,
        target_lng=118.95988,
        target_alt=30,
        test_home=True,
    )

    assert [_message_id(packet) for packet in (clear_packet, count_packet, *item_packets)] == [45, 44, 73, 73]
    count_payload = count_packet[10:-2]
    assert int.from_bytes(count_payload[0:2], "little") == 2
    home_payload = item_packets[0][10:-2]
    target_payload = item_packets[1][10:-2]
    assert int.from_bytes(home_payload[28:30], "little") == 0
    assert int.from_bytes(home_payload[16:20], "little", signed=True) == int(32.11956 * 1e7)
    assert int.from_bytes(home_payload[20:24], "little", signed=True) == int(118.958406 * 1e7)
    assert int.from_bytes(target_payload[28:30], "little") == 1
    assert int.from_bytes(target_payload[16:20], "little", signed=True) == int(32.12012 * 1e7)
    assert int.from_bytes(target_payload[20:24], "little", signed=True) == int(118.95988 * 1e7)
    assert round(__import__("struct").unpack_from("<f", target_payload, 24)[0], 1) == 30.0


def test_temporary_origin_packets_match_mission_planner_style_home_setup():
    packets = _build_temporary_origin_packets(lat=32.11956, lng=118.958406, alt=0)

    assert [_message_id(packet) for packet in packets] == [48, 75, 76]
    assert all(packet[0] == 0xFD for packet in packets)
    assert len(packets[0]) == 25
    assert len(packets[1]) == 47
    assert len(packets[2]) == 45


def test_origin_assist_is_used_without_recent_aircraft_position(tmp_path: Path):
    state_path = tmp_path / "aircraft.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "messages": [
                    {"time": time.time(), "type": "STATUSTEXT", "text": "状态 PreArm: GPS 1: Bad fix"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _needs_temporary_origin(state_path, time.time())


def test_origin_assist_is_skipped_with_recent_global_position(tmp_path: Path):
    state_path = tmp_path / "aircraft.json"
    state_path.write_text(
        json.dumps(
            {
                "updated_at": time.time(),
                "messages": [
                    {"time": time.time(), "type": "GLOBAL_POSITION_INT", "text": "位置 32.1195600, 118.9584060 高 2.0m"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert not _needs_temporary_origin(state_path, time.time())


class MissionSocketTests(unittest.TestCase):
    def test_mission_upload_can_read_request_from_command_socket(self):
        class ReplySocket:
            def __init__(self):
                payload = bytes([1, 0, 255, 190, 0])
                header = bytes([0xFD, len(payload), 0, 0, 0, 1, 1, 51, 0, 0])
                self.replies = [header + payload + b"\x00\x00"]

            def recvfrom(self, _size):
                if not self.replies:
                    raise BlockingIOError()
                return self.replies.pop(0), ("192.168.4.1", 14555)

        events = []

        _drain_mission_socket(ReplySocket(), events)

        self.assertEqual(events, [{"type": "MISSION_REQUEST_INT", "seq": 1, "result": None}])


def test_send_reports_mavlink2_packet_lengths(tmp_path: Path):
    FakeSocket.instances = []
    state_path = tmp_path / "aircraft.json"
    state_path.write_text(
        json.dumps(
            {
                "last_remote": "127.0.0.1:14561 -> UDP 14562",
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    with patch("aircraft_commands.socket.socket", FakeSocket):
        message = send_aircraft_command("aircraft_loiter", state_path)

    assert "MAVLink2 packets" in message
    assert "lens=45,18" in message
    assert "from UDP 14562" in message
    fake_socket = FakeSocket.instances[0]
    assert fake_socket.bound == ("0.0.0.0", 14562)
    assert len(fake_socket.sent) == 24
    assert {address for _packet, address in fake_socket.sent} == {
        ("127.0.0.1", 14561),
        ("127.0.0.1", 14555),
        ("127.0.0.1", 14550),
        ("127.0.0.1", 14560),
    }
    assert all(packet[0] == 0xFD for packet, _address in fake_socket.sent)
