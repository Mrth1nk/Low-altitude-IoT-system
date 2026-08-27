#!/usr/bin/env python3
import concurrent.futures
import socket


HOST = "192.168.4.1"


def check(port):
    sock = socket.socket()
    sock.settimeout(0.12)
    try:
        return port if sock.connect_ex((HOST, port)) == 0 else None
    finally:
        sock.close()


with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
    open_ports = [
        port for port in pool.map(check, range(1, 20001)) if port is not None
    ]
print("OPEN_TCP_PORTS=" + ",".join(map(str, open_ports)), flush=True)
