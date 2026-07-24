import os
import select
import termios
import time


def open_serial(port: str):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[2] |= termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[1] = 0
    attrs[0] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def at_command(port: str, command: str, wait: float = 0.8) -> str:
    fd = open_serial(port)
    try:
        try:
            while select.select([fd], [], [], 0)[0]:
                os.read(fd, 4096)
        except Exception:
            pass
        os.write(fd, (command + "\r").encode())
        time.sleep(wait)
        out = b""
        deadline = time.time() + 2.5
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                out += os.read(fd, 4096)
            elif out:
                break
        return out.decode(errors="ignore")
    finally:
        os.close(fd)


def parse_csq_rssi(output: str) -> int:
    marker = "+CSQ:"
    if marker not in output:
        return 0
    try:
        raw = int(output.split(marker, 1)[1].split(",", 1)[0].strip())
    except Exception:
        return 0
    if raw == 99:
        return 0
    return -113 + 2 * raw


def read_lte_rssi(at_port: str) -> int:
    return parse_csq_rssi(at_command(at_port, "AT+CSQ", 0.5))


def ensure_l610_usbnet(at_port: str, cid: int = 1) -> None:
    mode = at_command(at_port, "AT+GTUSBMODE?", 1.0)
    if not any(f"+GTUSBMODE: {m}" in mode for m in (32, 33)):
        raise RuntimeError("L610 USB mode is not 32/ECM or 33/RNDIS.")
    current = at_command(at_port, "AT+GTRNDIS?", 1.0)
    if f"+GTRNDIS: 1,{cid}" in current:
        return
    at_command(at_port, f'AT+CGDCONT={cid},"IP","3GNET"', 0.8)
    out = at_command(at_port, f"AT+GTRNDIS=1,{cid}", 1.5)
    if "OK" not in out and "+GTRNDIS:" not in out:
        after = at_command(at_port, "AT+GTRNDIS?", 1.0)
        if f"+GTRNDIS: 1,{cid}" in after:
            return
        raise RuntimeError(f"failed to enable USB network: {out.strip()}")


def check_l610(at_port: str) -> int:
    commands = ["ATE0", "ATI", "AT+CPIN?", "AT+CSQ", "AT+COPS?", "AT+CEREG?", "AT+GTUSBMODE?", "AT+GTRNDIS?"]
    ok = True
    for cmd in commands:
        out = at_command(at_port, cmd, 1.0)
        print(f"--- {cmd}\n{out.strip()}")
        if cmd in ("AT+CPIN?", "AT+CEREG?") and "OK" not in out:
            ok = False
    return 0 if ok else 1
