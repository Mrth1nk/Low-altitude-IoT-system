"""Carry LIOT frames through MAVLink-only serial Wi-Fi telemetry modules."""

import struct


MESSAGE_ID = 248
CRC_EXTRA = 8
PAYLOAD_MARKER = 0x4C49
MAX_DATA_BYTES = 249

_sequence = 0


def _x25_crc(data):
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp ^= (tmp << 4) & 0xFF
        crc = (
            (crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)
        ) & 0xFFFF
    return crc


def wrap_liot_frame(data, source_system=254, source_component=191):
    global _sequence
    data = bytes(data)
    if len(data) > MAX_DATA_BYTES:
        raise ValueError("LIOT frame exceeds MAVLink V2_EXTENSION payload")
    payload = struct.pack("<HBBB", PAYLOAD_MARKER, 0, 0, 0) + data
    header = bytes(
        (
            0xFD,
            len(payload),
            0,
            0,
            _sequence,
            source_system & 0xFF,
            source_component & 0xFF,
            MESSAGE_ID & 0xFF,
            (MESSAGE_ID >> 8) & 0xFF,
            0,
        )
    )
    _sequence = (_sequence + 1) & 0xFF
    checksum = _x25_crc(header[1:] + payload + bytes((CRC_EXTRA,)))
    return header + payload + struct.pack("<H", checksum)


def unwrap_liot_frame(packet):
    packet = bytes(packet)
    if len(packet) < 17 or packet[0] != 0xFD:
        return None
    payload_length = packet[1]
    message_id = packet[7] | (packet[8] << 8) | (packet[9] << 16)
    if message_id != MESSAGE_ID or payload_length < 5:
        return None
    expected = 10 + payload_length + 2
    if len(packet) != expected:
        return None
    payload = packet[10 : 10 + payload_length]
    if struct.unpack_from("<H", payload)[0] != PAYLOAD_MARKER:
        return None
    supplied = struct.unpack_from("<H", packet, expected - 2)[0]
    actual = _x25_crc(packet[1 : expected - 2] + bytes((CRC_EXTRA,)))
    if supplied != actual:
        return None
    return payload[5:]
