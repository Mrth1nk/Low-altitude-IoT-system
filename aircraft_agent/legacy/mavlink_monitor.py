"""Non-intrusive MAVLink status parser used only for bridge logs."""

from __future__ import annotations

import time
import struct


COPTER_MODE = {
    0: "STABILIZE",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    9: "LAND",
}


class MAVLinkMonitor:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.last_heartbeat = 0.0
        self.status: dict = {"heartbeat": False}

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        events: list[dict] = []
        while True:
            frame = self._pop_frame()
            if frame is None:
                break
            event = self._parse_frame(frame)
            if event:
                events.append(event)
        return events

    def heartbeat_age(self) -> float | None:
        if not self.last_heartbeat:
            return None
        return time.time() - self.last_heartbeat

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

    def _parse_frame(self, frame: bytes) -> dict | None:
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
            event = {
                "type": "HEARTBEAT",
                "system_id": system_id,
                "component_id": component_id,
                "mode": COPTER_MODE.get(custom_mode, f"MODE_{custom_mode}"),
                "custom_mode": custom_mode,
                "armed": bool(base_mode & 0x80),
                "vehicle_type": payload[4],
                "autopilot": payload[5],
            }
            self.last_heartbeat = time.time()
            self.status.update(event, heartbeat=True)
            return event
        if msg_id == 1 and len(payload) >= 31:
            return {
                "type": "SYS_STATUS",
                "voltage_battery": int.from_bytes(payload[14:16], "little") / 1000.0,
                "battery_remaining": int.from_bytes(payload[30:31], "little", signed=True),
            }
        if msg_id == 33 and len(payload) >= 28:
            return {
                "type": "GLOBAL_POSITION_INT",
                "lat": int.from_bytes(payload[4:8], "little", signed=True) / 1e7,
                "lon": int.from_bytes(payload[8:12], "little", signed=True) / 1e7,
                "altitude": int.from_bytes(payload[16:20], "little", signed=True) / 1000.0,
            }
        if msg_id == 11 and len(payload) >= 6:
            custom_mode = int.from_bytes(payload[2:6], "little")
            return {
                "type": "SET_MODE",
                "system_id": system_id,
                "component_id": component_id,
                "target_system": payload[0],
                "base_mode": payload[1],
                "custom_mode": custom_mode,
                "mode": COPTER_MODE.get(custom_mode, f"MODE_{custom_mode}"),
            }
        if msg_id == 76 and len(payload) >= 33:
            return {
                "type": "COMMAND_LONG",
                "system_id": system_id,
                "component_id": component_id,
                "target_system": payload[30],
                "target_component": payload[31],
                "command": int.from_bytes(payload[28:30], "little"),
                "confirmation": payload[32],
                "param1": struct.unpack("<f", payload[0:4])[0],
            }
        if msg_id == 77 and len(payload) >= 3:
            return {
                "type": "COMMAND_ACK",
                "system_id": system_id,
                "component_id": component_id,
                "command": int.from_bytes(payload[0:2], "little"),
                "result": payload[2],
            }
        if msg_id == 42 and len(payload) >= 2:
            return {
                "type": "MISSION_CURRENT",
                "system_id": system_id,
                "component_id": component_id,
                "seq": int.from_bytes(payload[0:2], "little"),
            }
        if msg_id == 44 and len(payload) >= 4:
            return {
                "type": "MISSION_COUNT",
                "system_id": system_id,
                "component_id": component_id,
                "count": int.from_bytes(payload[0:2], "little"),
                "target_system": payload[2],
                "target_component": payload[3],
                "mission_type": payload[4] if len(payload) >= 5 else 0,
            }
        if msg_id == 45 and len(payload) >= 2:
            return {
                "type": "MISSION_CLEAR_ALL",
                "system_id": system_id,
                "component_id": component_id,
                "target_system": payload[0],
                "target_component": payload[1],
                "mission_type": payload[2] if len(payload) >= 3 else 0,
            }
        if msg_id in (40, 51) and len(payload) >= 4:
            return {
                "type": "MISSION_REQUEST_INT" if msg_id == 51 else "MISSION_REQUEST",
                "system_id": system_id,
                "component_id": component_id,
                "seq": int.from_bytes(payload[0:2], "little"),
                "target_system": payload[2],
                "target_component": payload[3],
                "mission_type": payload[4] if len(payload) >= 5 else 0,
            }
        if msg_id == 47 and len(payload) >= 3:
            return {
                "type": "MISSION_ACK",
                "system_id": system_id,
                "component_id": component_id,
                "target_system": payload[0],
                "target_component": payload[1],
                "result": payload[2],
                "mission_type": payload[3] if len(payload) >= 4 else 0,
            }
        if msg_id == 73 and len(payload) >= 37:
            return {
                "type": "MISSION_ITEM_INT",
                "system_id": system_id,
                "component_id": component_id,
                "seq": int.from_bytes(payload[28:30], "little"),
                "command": int.from_bytes(payload[30:32], "little"),
                "target_system": payload[32],
                "target_component": payload[33],
                "frame": payload[34],
                "lat": int.from_bytes(payload[16:20], "little", signed=True) / 1e7,
                "lon": int.from_bytes(payload[20:24], "little", signed=True) / 1e7,
                "alt": struct.unpack("<f", payload[24:28])[0],
                "mission_type": payload[37] if len(payload) >= 38 else 0,
            }
        return None
