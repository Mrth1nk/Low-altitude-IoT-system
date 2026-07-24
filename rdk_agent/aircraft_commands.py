from __future__ import annotations

import json
import re
import socket
import struct
import time
from pathlib import Path

CRC_EXTRA = {
    0: 50,
    11: 89,
    44: 221,
    45: 232,
    48: 41,
    75: 158,
    76: 152,
    73: 38,
    86: 5,
}

MODE_ID_BY_COMMAND = {
    "aircraft_guided": ("GUIDED", 4),
    "aircraft_loiter": ("LOITER", 5),
    "aircraft_auto": ("AUTO", 3),
    "aircraft_rtl": ("RTL", 6),
    "aircraft_land": ("LAND", 9),
}

MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MAV_CMD_DO_SET_MODE = 176
MAV_CMD_DO_SET_HOME = 179
MAV_CMD_COMPONENT_ARM_DISARM = 400
MAV_FRAME_GLOBAL_RELATIVE_ALT_INT = 6
POSITION_ONLY_TYPE_MASK = 0b0000111111111000
MAV_CMD_NAV_WAYPOINT = 16
MISSION_TYPE_MISSION = 0
MISSION_ACK_ACCEPTED = 0
MAV_FRAME_GLOBAL = 0
TEST_HOME_LAT = 32.11956
TEST_HOME_LNG = 118.958406
TEST_HOME_ALT = 0.0
GPS_FIX_WARNING_TEXT = ("Bad fix", "GPS", "PreArm")
ORIGIN_ASSIST_COOLDOWN_SEC = 90.0


_sequence = 0
_last_origin_assist_at = 0.0


def _last_aircraft_remote(state_path: Path, stale_after_sec: float = 300.0) -> tuple[str, int, int]:
    doc = json.loads(state_path.read_text(encoding="utf-8"))
    last_remote = str(doc.get("last_remote", ""))
    remote = last_remote.split(" -> ", 1)[0]
    host, port = remote.rsplit(":", 1)
    local_port_match = re.search(r"UDP\s+(\d+)", last_remote)
    local_port = int(local_port_match.group(1)) if local_port_match else 14560
    updated_at = float(doc.get("updated_at", 0) or 0)
    if time.time() - updated_at > stale_after_sec:
        raise RuntimeError("aircraft telemetry link is stale")
    return host, int(port), local_port


def _x25_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def _next_seq() -> int:
    global _sequence
    value = _sequence
    _sequence = (_sequence + 1) & 0xFF
    return value


def _build_mavlink2_packet(
    msg_id: int,
    payload: bytes,
    *,
    source_system: int = 255,
    source_component: int = 190,
) -> bytes:
    if msg_id not in CRC_EXTRA:
        raise RuntimeError(f"missing MAVLink CRC extra for message {msg_id}")
    header = bytes(
        [
            0xFD,
            len(payload),
            0,
            0,
            _next_seq(),
            source_system & 0xFF,
            source_component & 0xFF,
            msg_id & 0xFF,
            (msg_id >> 8) & 0xFF,
            (msg_id >> 16) & 0xFF,
        ]
    )
    crc_input = header[1:] + payload + bytes([CRC_EXTRA[msg_id]])
    return header + payload + struct.pack("<H", _x25_crc(crc_input))


def _build_command_long(
    command: int,
    params: list[float],
    *,
    target_system: int,
    target_component: int,
) -> bytes:
    payload = b"".join(struct.pack("<f", value) for value in (params + [0.0] * 7)[:7])
    payload += struct.pack("<HBBB", command, target_system & 0xFF, target_component & 0xFF, 1)
    return _build_mavlink2_packet(76, payload)


def _build_set_mode(*, target_system: int, custom_mode: int) -> bytes:
    payload = struct.pack("<BBI", target_system & 0xFF, MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, custom_mode)
    return _build_mavlink2_packet(11, payload)


def _build_do_set_mode(*, target_system: int, target_component: int, custom_mode: int) -> bytes:
    return _build_command_long(
        MAV_CMD_DO_SET_MODE,
        [float(MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), float(custom_mode), 0.0, 0.0, 0.0, 0.0, 0.0],
        target_system=target_system,
        target_component=target_component,
    )


def _build_do_set_home(
    *,
    target_system: int,
    target_component: int,
    lat: float,
    lng: float,
    alt: float,
) -> bytes:
    return _build_command_long(
        MAV_CMD_DO_SET_HOME,
        [0.0, 0.0, 0.0, 0.0, float(lat), float(lng), float(alt)],
        target_system=target_system,
        target_component=target_component,
    )


def _build_command_int_do_set_home(
    *,
    target_system: int,
    target_component: int,
    lat: float,
    lng: float,
    alt: float,
) -> bytes:
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
        target_system & 0xFF,
        target_component & 0xFF,
        MAV_FRAME_GLOBAL,
        0,
        0,
    )
    return _build_mavlink2_packet(75, payload)


def _build_set_gps_global_origin(*, target_system: int, lat: float, lng: float, alt: float) -> bytes:
    payload = struct.pack(
        "<iiiB",
        int(lat * 1e7),
        int(lng * 1e7),
        int(alt * 1000),
        target_system & 0xFF,
    )
    return _build_mavlink2_packet(48, payload)


def _build_set_position_target_global_int(
    *,
    target_system: int,
    target_component: int,
    lat: float,
    lng: float,
    alt: float,
) -> bytes:
    payload = struct.pack(
        "<IiifffffffffHBBB",
        int(time.time() * 1000) & 0xFFFFFFFF,
        int(lat * 1e7),
        int(lng * 1e7),
        float(alt),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        POSITION_ONLY_TYPE_MASK,
        target_system & 0xFF,
        target_component & 0xFF,
        MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
    )
    return _build_mavlink2_packet(86, payload)


def _build_mission_count(*, target_system: int, target_component: int, count: int) -> bytes:
    payload = struct.pack(
        "<HBBB",
        count,
        target_system & 0xFF,
        target_component & 0xFF,
        MISSION_TYPE_MISSION,
    )
    return _build_mavlink2_packet(44, payload)


def _build_mission_clear_all(*, target_system: int, target_component: int) -> bytes:
    payload = struct.pack("<BBB", target_system & 0xFF, target_component & 0xFF, MISSION_TYPE_MISSION)
    return _build_mavlink2_packet(45, payload)


def _build_mission_item_int(
    *,
    target_system: int,
    target_component: int,
    seq: int,
    lat: float,
    lng: float,
    alt: float,
    current: int = 0,
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
        target_system & 0xFF,
        target_component & 0xFF,
        MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        current & 0xFF,
        1,
        MISSION_TYPE_MISSION,
    )
    return _build_mavlink2_packet(73, payload)


def build_aircraft_mission_packets(
    *,
    target_system: int = 1,
    target_component: int = 1,
    target_lat: float,
    target_lng: float,
    target_alt: float,
    test_home: bool = False,
) -> tuple[bytes, bytes, list[bytes]]:
    if abs(target_lat) < 0.000001 or abs(target_lng) < 0.000001:
        raise RuntimeError("aircraft mission invalid target")
    waypoints = []
    if test_home:
        waypoints.append((TEST_HOME_LAT, TEST_HOME_LNG, TEST_HOME_ALT))
    waypoints.append((target_lat, target_lng, target_alt))
    return (
        _build_mission_clear_all(target_system=target_system, target_component=target_component),
        _build_mission_count(target_system=target_system, target_component=target_component, count=len(waypoints)),
        [
            _build_mission_item_int(
                target_system=target_system,
                target_component=target_component,
                seq=seq,
                lat=lat,
                lng=lng,
                alt=alt,
                current=1 if seq == 0 else 0,
            )
            for seq, (lat, lng, alt) in enumerate(waypoints)
        ],
    )


def _mavlink_frames(data: bytes) -> list[tuple[int, bytes]]:
    frames = []
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


def _mission_request_seq(msg_id: int, payload: bytes) -> int | None:
    if msg_id not in (40, 51) or len(payload) < 2:
        return None
    return struct.unpack_from("<H", payload, 0)[0]


def _mission_ack_type(msg_id: int, payload: bytes) -> int | None:
    if msg_id != 47 or len(payload) < 3:
        return None
    return payload[2]


def _open_udp_socket(local_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("0.0.0.0", local_port))
    return sock


def _send_packets_on_socket(
    sock: socket.socket,
    host: str,
    ports: list[int],
    packets: list[bytes],
    repeats: int = 1,
) -> None:
    for target_port in ports:
        for packet in packets:
            for _ in range(repeats):
                sock.sendto(packet, (host, target_port))
                time.sleep(0.03)


def _drain_mission_socket(sock: socket.socket, events: list[dict]) -> None:
    while True:
        try:
            data, _remote = sock.recvfrom(4096)
        except (BlockingIOError, TimeoutError):
            return
        except OSError:
            return
        for msg_id, payload in _mavlink_frames(data):
            seq = _mission_request_seq(msg_id, payload)
            if seq is not None:
                events.append(
                    {
                        "type": "MISSION_REQUEST_INT" if msg_id == 51 else "MISSION_REQUEST",
                        "seq": seq,
                        "result": None,
                    }
                )
                continue
            ack = _mission_ack_type(msg_id, payload)
            if ack is not None:
                events.append({"type": "MISSION_ACK", "seq": None, "result": ack})


def _send_packet_burst(host: str, ports: list[int], local_port: int, packets: list[bytes], repeats: int = 1) -> None:
    sock = _open_udp_socket(local_port)
    try:
        _send_packets_on_socket(sock, host, ports, packets, repeats)
    finally:
        sock.close()


def _mission_events_from_state(state_path: Path, since: float) -> list[dict]:
    try:
        doc = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    events = []
    for item in doc.get("messages", []):
        if not isinstance(item, dict):
            continue
        if float(item.get("time", 0) or 0) < since:
            continue
        if item.get("type") in ("MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"):
            events.append(item)
    return events


def _needs_temporary_origin(state_path: Path, since: float) -> bool:
    try:
        doc = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    messages = [item for item in doc.get("messages", []) if isinstance(item, dict)]
    recent = [item for item in messages if float(item.get("time", 0) or 0) >= since - 30.0]
    if any(item.get("type") == "GLOBAL_POSITION_INT" for item in recent):
        return False
    for item in recent[-12:]:
        text = str(item.get("text", ""))
        if "Bad fix" in text or ("PreArm" in text and "GPS" in text):
            return True
    return True


def _build_temporary_origin_packets(
    *,
    lat: float,
    lng: float,
    alt: float = 0.0,
    target_system: int = 1,
    target_component: int = 1,
) -> list[bytes]:
    return [
        _build_set_gps_global_origin(target_system=target_system, lat=lat, lng=lng, alt=alt),
        _build_command_int_do_set_home(
            target_system=target_system,
            target_component=target_component,
            lat=lat,
            lng=lng,
            alt=alt,
        ),
        _build_do_set_home(
            target_system=target_system,
            target_component=target_component,
            lat=lat,
            lng=lng,
            alt=alt,
        ),
    ]


def _should_send_origin_assist(state_path: Path, since: float, force: bool = False) -> bool:
    global _last_origin_assist_at
    if force:
        _last_origin_assist_at = time.time()
        return True
    if not _needs_temporary_origin(state_path, since):
        return False
    now = time.time()
    if now - _last_origin_assist_at < ORIGIN_ASSIST_COOLDOWN_SEC:
        return False
    _last_origin_assist_at = now
    return True


def _wait_mission_request(
    state_path: Path,
    since: float,
    wanted: set[int],
    deadline: float,
    *,
    sock: socket.socket | None = None,
    socket_events: list[dict] | None = None,
) -> int | None:
    while time.time() < deadline:
        if sock is not None and socket_events is not None:
            _drain_mission_socket(sock, socket_events)
            for event in socket_events:
                if event.get("type") not in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                    continue
                try:
                    seq = int(event.get("seq"))
                except Exception:
                    continue
                if seq in wanted:
                    return seq
        for event in _mission_events_from_state(state_path, since):
            if event.get("type") not in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                continue
            try:
                seq = int(event.get("seq"))
            except Exception:
                continue
            if seq in wanted:
                return seq
        time.sleep(0.05)
    return None


def _wait_mission_ack(
    state_path: Path,
    since: float,
    deadline: float,
    *,
    sock: socket.socket | None = None,
    socket_events: list[dict] | None = None,
) -> int | None:
    while time.time() < deadline:
        if sock is not None and socket_events is not None:
            _drain_mission_socket(sock, socket_events)
            for event in socket_events:
                if event.get("type") != "MISSION_ACK":
                    continue
                try:
                    return int(event.get("result"))
                except Exception:
                    return None
        for event in _mission_events_from_state(state_path, since):
            if event.get("type") != "MISSION_ACK":
                continue
            try:
                return int(event.get("result"))
            except Exception:
                return None
        time.sleep(0.05)
    return None


def build_aircraft_command_packets(
    command: str,
    target_system: int = 1,
    target_component: int = 1,
    *,
    target_lat: float | None = None,
    target_lng: float | None = None,
    target_alt: float | None = None,
) -> list[bytes]:
    if command in MODE_ID_BY_COMMAND:
        _label, custom_mode = MODE_ID_BY_COMMAND[command]
        return [
            _build_do_set_mode(
                target_system=target_system,
                target_component=target_component,
                custom_mode=custom_mode,
            ),
            _build_set_mode(target_system=target_system, custom_mode=custom_mode),
        ]
    if command in ("aircraft_arm", "aircraft_disarm"):
        return [
            _build_command_long(
                MAV_CMD_COMPONENT_ARM_DISARM,
                [1.0 if command == "aircraft_arm" else 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                target_system=target_system,
                target_component=target_component,
            )
        ]
    if command == "aircraft_goto":
        if target_lat is None or target_lng is None or target_alt is None:
            raise RuntimeError("aircraft_goto missing target lat/lng/alt")
        if abs(target_lat) < 0.000001 or abs(target_lng) < 0.000001:
            raise RuntimeError("aircraft_goto invalid target")
        guided_packets = build_aircraft_command_packets(
            "aircraft_guided",
            target_system=target_system,
            target_component=target_component,
        )
        goto_packet = _build_set_position_target_global_int(
            target_system=target_system,
            target_component=target_component,
            lat=target_lat,
            lng=target_lng,
            alt=max(1.0, min(120.0, float(target_alt))),
        )
        return guided_packets + [goto_packet]
    raise RuntimeError(f"unknown aircraft command {command}")


def send_aircraft_command(
    command: str,
    state_path: Path,
    *,
    target_lat: float | None = None,
    target_lng: float | None = None,
    target_alt: float | None = None,
) -> str:
    host, port, local_port = _last_aircraft_remote(state_path)
    if command in ("aircraft_goto", "aircraft_goto_test_home"):
        if target_lat is None or target_lng is None or target_alt is None:
            raise RuntimeError(f"{command} missing target lat/lng/alt")
        return _send_aircraft_auto_mission(
            host=host,
            port=port,
            local_port=local_port,
            state_path=state_path,
            target_lat=target_lat,
            target_lng=target_lng,
            target_alt=target_alt,
            test_home=(command == "aircraft_goto_test_home"),
        )
    packets = build_aircraft_command_packets(
        command,
        target_lat=target_lat,
        target_lng=target_lng,
        target_alt=target_alt,
    )
    if not packets or any(not packet for packet in packets):
        raise RuntimeError(f"built empty MAVLink2 packet for {command}")
    if any(packet[0] != 0xFD for packet in packets):
        raise RuntimeError(f"built non-MAVLink2 packet for {command}")
    ports = [port, 14555, 14550, 14560]
    ports = list(dict.fromkeys(ports))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    try:
        sock.bind(("0.0.0.0", local_port))
        for target_port in ports:
            for packet in packets:
                for _ in range(3):
                    sock.sendto(packet, (host, target_port))
                    time.sleep(0.03)
    finally:
        sock.close()
    lengths = ",".join(str(len(packet)) for packet in packets)
    return (
        f"aircraft command {command} sent {len(packets)} MAVLink2 packets "
        f"lens={lengths} from UDP {local_port} to {host}:{','.join(str(p) for p in ports)}"
    )


def _send_aircraft_auto_mission(
    *,
    host: str,
    port: int,
    local_port: int,
    state_path: Path,
    target_lat: float,
    target_lng: float,
    target_alt: float,
    test_home: bool = False,
) -> str:
    clear_packet, count_packet, item_packets = build_aircraft_mission_packets(
        target_lat=target_lat,
        target_lng=target_lng,
        target_alt=target_alt,
        test_home=test_home,
    )
    auto_packets = build_aircraft_command_packets("aircraft_auto")
    ports = list(dict.fromkeys([port, 14555, 14550, 14560]))
    requested_sequences: set[int] = set()
    socket_events: list[dict] = []
    since = time.time()
    use_temporary_origin = _should_send_origin_assist(state_path, since, force=test_home)
    origin_lat = TEST_HOME_LAT if test_home else target_lat
    origin_lng = TEST_HOME_LNG if test_home else target_lng
    origin_alt = TEST_HOME_ALT
    origin_packets = _build_temporary_origin_packets(lat=origin_lat, lng=origin_lng, alt=origin_alt) if use_temporary_origin else []
    sock = _open_udp_socket(local_port)
    sock.setblocking(False)
    try:
        if origin_packets:
            _send_packets_on_socket(sock, host, ports, origin_packets, repeats=3)
        _send_packets_on_socket(sock, host, ports, [clear_packet], repeats=1)

        deadline = time.time() + 10.0
        while time.time() < deadline and len(requested_sequences) < len(item_packets):
            _send_packets_on_socket(sock, host, ports, [count_packet], repeats=1)
            seq = _wait_mission_request(
                state_path,
                since,
                set(range(len(item_packets))) - requested_sequences,
                min(time.time() + 1.0, deadline),
                sock=sock,
                socket_events=socket_events,
            )
            if seq is None:
                continue
            requested_sequences.add(seq)
            _send_packets_on_socket(sock, host, ports, [item_packets[seq]], repeats=3)

        if not requested_sequences:
            raise RuntimeError(
                "aircraft mission upload failed: sent MISSION_COUNT "
                f"count={len(item_packets)} to {host}:{','.join(str(p) for p in ports)} "
                f"from UDP {local_port}, but no MISSION_REQUEST_INT arrived"
            )
        if requested_sequences != set(range(len(item_packets))):
            raise RuntimeError(
                "aircraft mission upload failed: "
                f"requested seqs={sorted(requested_sequences)} expected={list(range(len(item_packets)))}"
            )

        ack_type = _wait_mission_ack(
            state_path,
            since,
            time.time() + 5.0,
            sock=sock,
            socket_events=socket_events,
        )
        if ack_type is None:
            raise RuntimeError(
                "aircraft mission upload failed: sent all requested MISSION_ITEM_INT "
                f"seqs={sorted(requested_sequences)}, but no MISSION_ACK arrived"
            )
        if ack_type != MISSION_ACK_ACCEPTED:
            raise RuntimeError(f"aircraft mission upload rejected ack={ack_type}")

        _send_packets_on_socket(sock, host, ports, auto_packets, repeats=3)
    finally:
        sock.close()
    return (
        f"aircraft {'test-home ' if test_home else ''}mission uploaded and AUTO sent "
        f"count={len(item_packets)} "
        f"origin_assist={'yes' if use_temporary_origin else 'no'} "
        f"target={target_lat:.7f},{target_lng:.7f},{max(1.0, min(120.0, float(target_alt))):.1f}m "
        f"from UDP {local_port} to {host}:{','.join(str(p) for p in ports)}"
    )
