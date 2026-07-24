#!/usr/bin/env python3
"""Direct MAVLink command test for the flight-controller USB serial port."""

from __future__ import annotations

import argparse
import struct
import time


CRC_EXTRA = {
    0: 50,
    11: 89,
    44: 221,
    45: 232,
    48: 41,
    73: 38,
    75: 158,
    76: 152,
    77: 143,
}

COPTER_MODE_ID = {
    "STABILIZE": 0,
    "ALT_HOLD": 2,
    "AUTO": 3,
    "GUIDED": 4,
    "LOITER": 5,
    "RTL": 6,
    "LAND": 9,
}

MAV_CMD_COMPONENT_ARM_DISARM = 400
MAV_CMD_DO_SET_MODE = 176
MAV_CMD_DO_SET_HOME = 179
MAV_CMD_NAV_WAYPOINT = 16
MAV_FRAME_GLOBAL = 0
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
MISSION_TYPE_MISSION = 0
MISSION_ACK_ACCEPTED = 0
TEST_HOME_LAT = 32.11956
TEST_HOME_LNG = 118.958406
TEST_HOME_ALT = 0.0

ACK_RESULT = {
    0: "ACCEPTED",
    1: "TEMPORARILY_REJECTED",
    2: "DENIED",
    3: "UNSUPPORTED",
    4: "FAILED",
    5: "IN_PROGRESS",
    6: "CANCELLED",
}


def x25_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def build_packet(msg_id: int, payload: bytes, seq: int, system_id: int = 255, component_id: int = 190) -> bytes:
    header = bytes(
        [
            0xFD,
            len(payload),
            0,
            0,
            seq & 0xFF,
            system_id & 0xFF,
            component_id & 0xFF,
            msg_id & 0xFF,
            (msg_id >> 8) & 0xFF,
            (msg_id >> 16) & 0xFF,
        ]
    )
    checksum_input = header[1:] + payload + bytes([CRC_EXTRA[msg_id]])
    crc = struct.pack("<H", x25_crc(checksum_input))
    return header + payload + crc


def build_gcs_heartbeat(seq: int) -> bytes:
    payload = struct.pack("<IBBBBB", 0, 6, 8, 0, 4, 3)
    return build_packet(0, payload, seq)


def build_set_mode(seq: int, target_system: int, mode: str) -> bytes:
    custom_mode = COPTER_MODE_ID[mode.upper()]
    payload = struct.pack("<BBI", target_system, 1, custom_mode)
    return build_packet(11, payload, seq)


def build_do_set_mode(seq: int, target_system: int, target_component: int, mode: str) -> bytes:
    custom_mode = COPTER_MODE_ID[mode.upper()]
    params = [1.0, float(custom_mode), 0.0, 0.0, 0.0, 0.0, 0.0]
    payload = b"".join(struct.pack("<f", value) for value in params)
    payload += struct.pack("<HBBB", MAV_CMD_DO_SET_MODE, target_system, target_component, 0)
    return build_packet(76, payload, seq)


def build_arm(seq: int, target_system: int, target_component: int, arm: bool) -> bytes:
    params = [1.0 if arm else 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    payload = b"".join(struct.pack("<f", value) for value in params)
    payload += struct.pack("<HBBB", MAV_CMD_COMPONENT_ARM_DISARM, target_system, target_component, 0)
    return build_packet(76, payload, seq)


def build_set_gps_origin(seq: int, target_system: int, lat: float, lng: float, alt: float) -> bytes:
    payload = struct.pack("<iiiB", int(lat * 1e7), int(lng * 1e7), int(alt * 1000), target_system)
    return build_packet(48, payload, seq)


def build_command_int_set_home(seq: int, target_system: int, target_component: int, lat: float, lng: float, alt: float) -> bytes:
    payload = struct.pack(
        "<ffffiifHBBBBB",
        0.0,
        0.0,
        0.0,
        0.0,
        int(lat * 1e7),
        int(lng * 1e7),
        float(alt),
        MAV_CMD_DO_SET_HOME,
        target_system,
        target_component,
        MAV_FRAME_GLOBAL,
        0,
        0,
    )
    return build_packet(75, payload, seq)


def build_mission_clear_all(seq: int, target_system: int, target_component: int) -> bytes:
    return build_packet(45, struct.pack("<BBB", target_system, target_component, MISSION_TYPE_MISSION), seq)


def build_mission_count(seq: int, target_system: int, target_component: int, count: int) -> bytes:
    return build_packet(44, struct.pack("<HBBB", count, target_system, target_component, MISSION_TYPE_MISSION), seq)


def build_mission_item_int(
    seq_packet: int,
    *,
    target_system: int,
    target_component: int,
    seq: int,
    lat: float,
    lng: float,
    alt: float,
    current: int,
) -> bytes:
    payload = struct.pack(
        "<ffffiifHHBBBBBB",
        0.0,
        0.0,
        0.0,
        float("nan"),
        int(lat * 1e7),
        int(lng * 1e7),
        max(1.0, min(120.0, float(alt))),
        seq & 0xFFFF,
        MAV_CMD_NAV_WAYPOINT,
        target_system,
        target_component,
        MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        current,
        1,
        MISSION_TYPE_MISSION,
    )
    return build_packet(73, payload, seq_packet)


class Parser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        events: list[dict] = []
        while True:
            frame = self._pop_frame()
            if frame is None:
                break
            event = self._parse(frame)
            if event:
                events.append(event)
        return events

    def _pop_frame(self) -> bytes | None:
        while self.buffer and self.buffer[0] not in (0xFE, 0xFD):
            del self.buffer[0]
        if len(self.buffer) < 8:
            return None
        magic = self.buffer[0]
        length = self.buffer[1]
        if magic == 0xFD:
            if len(self.buffer) < 10:
                return None
            signature_len = 13 if self.buffer[2] & 0x01 else 0
            frame_len = 10 + length + 2 + signature_len
        else:
            frame_len = 6 + length + 2
        if len(self.buffer) < frame_len:
            return None
        frame = bytes(self.buffer[:frame_len])
        del self.buffer[:frame_len]
        return frame

    def _parse(self, frame: bytes) -> dict | None:
        magic = frame[0]
        length = frame[1]
        if magic == 0xFD:
            header_len = 10
            system_id = frame[5]
            component_id = frame[6]
            msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
        else:
            header_len = 6
            system_id = frame[3]
            component_id = frame[4]
            msg_id = frame[5]
        payload = frame[header_len : header_len + length]
        if msg_id == 0 and len(payload) >= 9:
            custom_mode = int.from_bytes(payload[0:4], "little")
            base_mode = payload[6]
            return {
                "type": "HEARTBEAT",
                "system_id": system_id,
                "component_id": component_id,
                "custom_mode": custom_mode,
                "armed": bool(base_mode & 0x80),
            }
        if msg_id == 77 and len(payload) >= 3:
            return {
                "type": "COMMAND_ACK",
                "system_id": system_id,
                "component_id": component_id,
                "command": int.from_bytes(payload[0:2], "little"),
                "result": payload[2],
            }
        if msg_id in (40, 51) and len(payload) >= 2:
            return {
                "type": "MISSION_REQUEST_INT" if msg_id == 51 else "MISSION_REQUEST",
                "system_id": system_id,
                "component_id": component_id,
                "seq": int.from_bytes(payload[0:2], "little"),
            }
        if msg_id == 47 and len(payload) >= 3:
            return {
                "type": "MISSION_ACK",
                "system_id": system_id,
                "component_id": component_id,
                "result": payload[2],
            }
        if msg_id == 253 and len(payload) >= 2:
            text = payload[1:51].split(b"\0", 1)[0].decode("utf-8", errors="replace")
            return {
                "type": "STATUSTEXT",
                "system_id": system_id,
                "component_id": component_id,
                "severity": payload[0],
                "text": text,
            }
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a direct MAVLink test command to the flight controller.")
    parser.add_argument("--fc", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=57600)
    parser.add_argument("--command", choices=["set-mode", "legacy-set-mode", "arm", "disarm", "mission-test-home"], default="set-mode")
    parser.add_argument("--mode", default="GUIDED", choices=sorted(COPTER_MODE_ID))
    parser.add_argument("--lat", type=float, default=32.11985)
    parser.add_argument("--lng", type=float, default=118.95875)
    parser.add_argument("--alt", type=float, default=20.0)
    parser.add_argument("--wait", type=float, default=8.0)
    args = parser.parse_args()

    import serial  # type: ignore

    port = serial.Serial(args.fc, args.baud, timeout=0.05, write_timeout=1)
    mav = Parser()
    seq = 0
    target_system = 1
    target_component = 1
    deadline = time.time() + args.wait
    print(f"[direct] opened {args.fc} @ {args.baud}")
    print("[direct] waiting for flight-controller heartbeat...")

    while time.time() < deadline:
        data = port.read(1024)
        for event in mav.feed(data):
            if event["type"] == "HEARTBEAT" and event["system_id"] != 255:
                target_system = event["system_id"]
                target_component = event["component_id"]
                print(
                    f"[direct] heartbeat system={target_system} component={target_component} "
                    f"mode={event['custom_mode']} armed={event['armed']}"
                )
                deadline = time.time()
                break

    port.write(build_gcs_heartbeat(seq))
    print(f"[direct] TX GCS_HEARTBEAT seq={seq}")
    seq = (seq + 1) & 0xFF

    if args.command == "mission-test-home":
        mission_items = [
            build_mission_item_int(
                seq,
                target_system=target_system,
                target_component=target_component,
                seq=0,
                lat=TEST_HOME_LAT,
                lng=TEST_HOME_LNG,
                alt=TEST_HOME_ALT,
                current=1,
            ),
            build_mission_item_int(
                (seq + 1) & 0xFF,
                target_system=target_system,
                target_component=target_component,
                seq=1,
                lat=args.lat,
                lng=args.lng,
                alt=args.alt,
                current=0,
            ),
        ]
        seq = (seq + 2) & 0xFF
        setup_packets = [
            build_set_gps_origin(seq, target_system, TEST_HOME_LAT, TEST_HOME_LNG, TEST_HOME_ALT),
            build_command_int_set_home((seq + 1) & 0xFF, target_system, target_component, TEST_HOME_LAT, TEST_HOME_LNG, TEST_HOME_ALT),
        ]
        seq = (seq + 2) & 0xFF
        for packet in setup_packets:
            port.write(packet)
            print(f"[direct] TX origin/home len={len(packet)} raw={packet.hex(' ').upper()}")
            time.sleep(0.1)
        clear = build_mission_clear_all(seq, target_system, target_component)
        seq = (seq + 1) & 0xFF
        count = build_mission_count(seq, target_system, target_component, len(mission_items))
        seq = (seq + 1) & 0xFF
        port.write(clear)
        print(f"[direct] TX MISSION_CLEAR_ALL len={len(clear)}")
        requested = set()
        ack_result = None
        flight_plan_received = False
        deadline = time.time() + args.wait
        next_count = 0.0
        while time.time() < deadline and ack_result is None:
            now = time.time()
            if len(requested) < len(mission_items) and now >= next_count:
                port.write(count)
                print(f"[direct] TX MISSION_COUNT count={len(mission_items)} len={len(count)}")
                next_count = now + 0.8
            try:
                data = port.read(1024)
            except Exception as exc:
                if flight_plan_received:
                    break
                print(f"[direct] serial read failed: {exc}")
                return 5
            for event in mav.feed(data):
                if event["type"] in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                    item_seq = event["seq"]
                    print(f"[direct] RX {event['type']} seq={item_seq}")
                    if 0 <= item_seq < len(mission_items):
                        requested.add(item_seq)
                        port.write(mission_items[item_seq])
                        print(f"[direct] TX MISSION_ITEM_INT seq={item_seq} len={len(mission_items[item_seq])}")
                elif event["type"] == "MISSION_ACK":
                    ack_result = event["result"]
                    print(f"[direct] RX MISSION_ACK result={ack_result}")
                    break
                elif event["type"] == "STATUSTEXT":
                    print(f"[direct] RX STATUSTEXT severity={event['severity']} text={event['text']}")
                    if "Flight plan received" in event["text"]:
                        flight_plan_received = True
                        ack_result = MISSION_ACK_ACCEPTED
                        break
            time.sleep(0.01)
        if ack_result != MISSION_ACK_ACCEPTED:
            print(f"[direct] mission upload failed requested={sorted(requested)} ack={ack_result}")
            return 4
        print(f"[direct] mission uploaded OK requested={sorted(requested)} target={args.lat:.7f},{args.lng:.7f},{args.alt:.1f}m")
        return 0

    target_mode = COPTER_MODE_ID[args.mode.upper()]
    if args.command == "set-mode":
        packet = build_do_set_mode(seq, target_system, target_component, args.mode)
        label = f"DO_SET_MODE:{args.mode}"
    elif args.command == "legacy-set-mode":
        packet = build_set_mode(seq, target_system, args.mode)
        label = f"LEGACY_SET_MODE:{args.mode}"
    elif args.command == "arm":
        packet = build_arm(seq, target_system, target_component, True)
        label = "ARM"
    else:
        packet = build_arm(seq, target_system, target_component, False)
        label = "DISARM"

    port.write(packet)
    print(f"[direct] TX {label} seq={seq} target={target_system}/{target_component} len={len(packet)} raw={packet.hex(' ').upper()}")

    deadline = time.time() + args.wait
    while time.time() < deadline:
        data = port.read(1024)
        for event in mav.feed(data):
            if event["type"] == "HEARTBEAT" and event["system_id"] == target_system:
                print(f"[direct] RX HEARTBEAT mode={event['custom_mode']} armed={event['armed']}")
                if args.command in ("set-mode", "legacy-set-mode") and event["custom_mode"] == target_mode:
                    print(f"[direct] mode changed to {args.mode}")
                    return 0
            elif event["type"] == "COMMAND_ACK":
                result = event["result"]
                print(f"[direct] RX COMMAND_ACK command={event['command']} result={result}({ACK_RESULT.get(result, 'UNKNOWN')})")
                if event["command"] in (MAV_CMD_DO_SET_MODE, MAV_CMD_COMPONENT_ARM_DISARM):
                    return 0 if result == 0 else 3
            elif event["type"] == "STATUSTEXT":
                print(f"[direct] RX STATUSTEXT severity={event['severity']} text={event['text']}")
        time.sleep(0.01)

    print("[direct] no COMMAND_ACK received before timeout")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
