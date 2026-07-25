from __future__ import annotations

import socket
import struct
import threading
import time
import json
from collections import deque
from pathlib import Path
from typing import Deque


COPTER_MODE = {
    0: "STABILIZE",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    9: "LAND",
}


class MavlinkParser:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict]:
        self.buffer.extend(chunk)
        frames = []
        offset = 0
        while offset <= len(self.buffer) - 8:
            magic = self.buffer[offset]
            if magic not in (0xFE, 0xFD):
                offset += 1
                continue
            payload_len = self.buffer[offset + 1]
            is_v2 = magic == 0xFD
            header_len = 10 if is_v2 else 6
            signature_len = 13 if is_v2 and (self.buffer[offset + 2] & 0x01) else 0
            frame_len = header_len + payload_len + 2 + signature_len
            if offset + frame_len > len(self.buffer):
                break
            frame = bytes(self.buffer[offset : offset + frame_len])
            msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16) if is_v2 else frame[5]
            system_id = frame[5] if is_v2 else frame[3]
            component_id = frame[6] if is_v2 else frame[4]
            payload = frame[header_len : header_len + payload_len]
            parsed = parse_payload(msg_id, system_id, component_id, payload)
            if parsed:
                frames.append(parsed)
            offset += frame_len
        if offset:
            del self.buffer[:offset]
        if len(self.buffer) > 4096:
            del self.buffer[:-512]
        return frames


def parse_payload(msg_id: int, system_id: int, component_id: int, payload: bytes) -> dict | None:
    try:
        if msg_id == 0 and len(payload) >= 9:
            custom_mode = struct.unpack_from("<I", payload, 0)[0]
            base_mode = payload[6]
            return {
                "type": "HEARTBEAT",
                "text": f"心跳 {COPTER_MODE.get(custom_mode, custom_mode)} armed={'YES' if base_mode & 0x80 else 'NO'} sys={system_id}/{component_id}",
            }
        if msg_id == 1 and len(payload) >= 31:
            voltage = struct.unpack_from("<H", payload, 14)[0] / 1000
            battery = struct.unpack_from("<b", payload, 30)[0]
            return {
                "type": "SYS_STATUS",
                "text": f"电池 {battery}% {voltage:.2f}V",
                "battery_percent": battery,
                "voltage": voltage,
            }
        if msg_id == 33 and len(payload) >= 28:
            lat = struct.unpack_from("<i", payload, 4)[0] / 1e7
            lon = struct.unpack_from("<i", payload, 8)[0] / 1e7
            rel_alt = struct.unpack_from("<i", payload, 16)[0] / 1000
            vx = struct.unpack_from("<h", payload, 20)[0] / 100
            vy = struct.unpack_from("<h", payload, 22)[0] / 100
            heading = struct.unpack_from("<H", payload, 26)[0]
            result = {
                "type": "GLOBAL_POSITION_INT",
                "text": f"位置 {lat:.7f}, {lon:.7f} 高 {rel_alt:.1f}m",
                "lat": lat,
                "lng": lon,
                "altitude": rel_alt,
                "ground_speed": (vx * vx + vy * vy) ** 0.5,
            }
            if heading != 65535:
                result["heading"] = heading / 100
            return result
        if msg_id == 74 and len(payload) >= 20:
            ground_speed = struct.unpack_from("<f", payload, 4)[0]
            heading = struct.unpack_from("<h", payload, 16)[0]
            throttle = struct.unpack_from("<H", payload, 18)[0]
            return {
                "type": "VFR_HUD",
                "text": f"速度 {ground_speed:.1f}m/s 航向 {heading} 油门 {throttle}%",
                "ground_speed": ground_speed,
                "heading": heading,
            }
        if msg_id == 147 and len(payload) >= 31:
            battery = struct.unpack_from("<b", payload, 30)[0]
            return {
                "type": "BATTERY_STATUS",
                "text": f"电池 {battery}%",
                "battery_percent": battery,
            }
        if msg_id == 253 and len(payload) >= 2:
            text = payload[1:51].split(b"\x00", 1)[0].decode("utf-8", "replace")
            return {"type": "STATUSTEXT", "text": f"状态 {text}"}
        if msg_id == 42 and len(payload) >= 2:
            seq = struct.unpack_from("<H", payload, 0)[0]
            return {
                "type": "MISSION_CURRENT",
                "text": f"当前任务 seq={seq}",
                "seq": seq,
            }
        if msg_id in (40, 51) and len(payload) >= 4:
            seq = struct.unpack_from("<H", payload, 0)[0]
            mission_type = payload[4] if len(payload) >= 5 else 0
            label = "MISSION_REQUEST_INT" if msg_id == 51 else "MISSION_REQUEST"
            return {"type": label, "text": f"请求任务项 seq={seq} type={mission_type}", "seq": seq, "mission_type": mission_type}
        if msg_id == 47 and len(payload) >= 3:
            result = payload[2]
            mission_type = payload[3] if len(payload) >= 4 else 0
            return {"type": "MISSION_ACK", "text": f"任务确认 result={result} type={mission_type}", "result": result, "mission_type": mission_type}
    except Exception:
        return None
    return None


class AircraftGatewayState:
    def __init__(self, max_messages: int = 80, state_path: Path | None = None) -> None:
        self.lock = threading.Lock()
        self.messages: Deque[dict] = deque(maxlen=max_messages)
        self.latest_heartbeat: dict | None = None
        self.parsers: dict[int, MavlinkParser] = {}
        self.bytes_received = 0
        self.packets_received = 0
        self.last_remote = ""
        self.last_seen = 0.0
        self.state_path = state_path

    def record_packet(self, port: int, data: bytes, remote: tuple[str, int]) -> bool:
        parser = self.parsers.setdefault(port, MavlinkParser())
        frames = parser.feed(data)
        now = time.time()
        with self.lock:
            self.bytes_received += len(data)
            self.packets_received += 1
            self.last_remote = f"{remote[0]}:{remote[1]} -> UDP {port}"
            self.last_seen = now
            if not frames:
                self.messages.append({"time": now, "text": f"收到数传 {len(data)}B", "type": "RAW"})
            for frame in frames:
                message = {"time": now, **frame}
                self.messages.append(message)
                if str(frame.get("type", "")).upper() == "HEARTBEAT":
                    self.latest_heartbeat = message
            self._write_snapshot_locked()
        return bool(frames)

    def snapshot(self) -> dict:
        with self.lock:
            age = time.time() - self.last_seen if self.last_seen else None
            return {
                "ok": True,
                "source": "rdk-wifi-udp-gateway",
                "updated_at": time.time(),
                "link_active": bool(age is not None and age < 3),
                "last_seen_age_sec": round(age, 2) if age is not None else None,
                "last_remote": self.last_remote,
                "bytes_received": self.bytes_received,
                "packets_received": self.packets_received,
                "messages": list(self.messages)[-24:],
                "latest_heartbeat": self.latest_heartbeat,
            }

    def _write_snapshot_locked(self) -> None:
        if not self.state_path:
            return
        age = time.time() - self.last_seen if self.last_seen else None
        doc = {
            "ok": True,
            "source": "rdk-wifi-udp-gateway",
            "updated_at": time.time(),
            "link_active": bool(age is not None and age < 3),
            "last_seen_age_sec": round(age, 2) if age is not None else None,
            "last_remote": self.last_remote,
            "bytes_received": self.bytes_received,
            "packets_received": self.packets_received,
            "messages": list(self.messages)[-24:],
            "latest_heartbeat": self.latest_heartbeat,
        }
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(self.state_path)


def udp_worker(state: AircraftGatewayState, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    while True:
        data, remote = sock.recvfrom(4096)
        state.record_packet(port, data, remote)


def start_aircraft_gateway(udp_ports: list[int], state_path: Path | None = None) -> AircraftGatewayState:
    state = AircraftGatewayState(state_path=state_path)
    for port in udp_ports:
        thread = threading.Thread(target=udp_worker, args=(state, port), daemon=True)
        thread.start()
    return state
