#!/usr/bin/env python3
"""Probe mission upload through the bridge WiFi receive path in UDP mode."""

from __future__ import annotations

import argparse
import socket
import time

from fc_direct_test import (
    MISSION_ACK_ACCEPTED,
    TEST_HOME_ALT,
    TEST_HOME_LAT,
    TEST_HOME_LNG,
    Parser,
    build_command_int_set_home,
    build_gcs_heartbeat,
    build_mission_clear_all,
    build_mission_count,
    build_mission_item_int,
    build_set_gps_origin,
)


def send(sock: socket.socket, peer: tuple[str, int], packet: bytes, label: str) -> None:
    sock.sendto(packet, peer)
    print(f"[probe] TX {label} len={len(packet)} raw={packet.hex(' ').upper()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate RDK mission upload into bridge UDP WiFi input.")
    parser.add_argument("--bridge-host", default="127.0.0.1")
    parser.add_argument("--bridge-port", type=int, default=14660)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=14661)
    parser.add_argument("--lat", type=float, default=32.11985)
    parser.add_argument("--lng", type=float, default=118.95875)
    parser.add_argument("--alt", type=float, default=20.0)
    parser.add_argument("--wait", type=float, default=12.0)
    args = parser.parse_args()

    peer = (args.bridge_host, args.bridge_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.listen_host, args.listen_port))
    sock.setblocking(False)
    mav = Parser()
    seq = 0
    target_system = 1
    target_component = 1
    requested: set[int] = set()
    ack_result: int | None = None

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
        ("GCS_HEARTBEAT", build_gcs_heartbeat(seq)),
        ("SET_GPS_GLOBAL_ORIGIN", build_set_gps_origin((seq + 1) & 0xFF, target_system, TEST_HOME_LAT, TEST_HOME_LNG, TEST_HOME_ALT)),
        (
            "COMMAND_INT_DO_SET_HOME",
            build_command_int_set_home(
                (seq + 2) & 0xFF,
                target_system,
                target_component,
                TEST_HOME_LAT,
                TEST_HOME_LNG,
                TEST_HOME_ALT,
            ),
        ),
    ]
    seq = (seq + 3) & 0xFF

    print(f"[probe] UDP local={args.listen_host}:{args.listen_port} bridge={peer[0]}:{peer[1]}")
    for label, packet in setup_packets:
        send(sock, peer, packet, label)
        time.sleep(0.12)

    clear = build_mission_clear_all(seq, target_system, target_component)
    seq = (seq + 1) & 0xFF
    count = build_mission_count(seq, target_system, target_component, len(mission_items))
    send(sock, peer, clear, "MISSION_CLEAR_ALL")

    deadline = time.time() + args.wait
    next_count = 0.0
    while time.time() < deadline and ack_result is None:
        now = time.time()
        if len(requested) < len(mission_items) and now >= next_count:
            send(sock, peer, count, f"MISSION_COUNT count={len(mission_items)}")
            next_count = now + 0.8

        try:
            data, remote = sock.recvfrom(4096)
            print(f"[probe] RX raw {len(data)}B from {remote[0]}:{remote[1]}")
        except BlockingIOError:
            time.sleep(0.02)
            continue

        for event in mav.feed(data):
            if event["type"] in ("MISSION_REQUEST", "MISSION_REQUEST_INT"):
                item_seq = int(event["seq"])
                print(f"[probe] RX {event['type']} seq={item_seq}")
                if 0 <= item_seq < len(mission_items):
                    requested.add(item_seq)
                    send(sock, peer, mission_items[item_seq], f"MISSION_ITEM_INT seq={item_seq}")
            elif event["type"] == "MISSION_ACK":
                ack_result = int(event["result"])
                print(f"[probe] RX MISSION_ACK result={ack_result}")
                break
            elif event["type"] == "STATUSTEXT":
                print(f"[probe] RX STATUSTEXT severity={event['severity']} text={event['text']}")
                if "Flight plan received" in event["text"]:
                    ack_result = MISSION_ACK_ACCEPTED
                    break
            elif event["type"] == "HEARTBEAT" and event["system_id"] == target_system:
                print(f"[probe] RX HEARTBEAT mode={event['custom_mode']} armed={event['armed']}")

    if ack_result != MISSION_ACK_ACCEPTED:
        print(f"[probe] mission handshake failed requested={sorted(requested)} ack={ack_result}")
        return 4
    print(f"[probe] mission handshake OK requested={sorted(requested)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
