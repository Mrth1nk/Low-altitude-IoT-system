#!/usr/bin/env python3
import argparse
import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from aircraft_gateway import start_aircraft_gateway
from rover_state import RoverCommand

ROOT = Path.home() / "uav_tuya_agent"
WEB_ROOT = ROOT / "web"
STATE_PATH = ROOT / "runtime_state.json"
COMMAND_PATH = ROOT / "command_inbox.jsonl"
AIRCRAFT_STATE_PATH = ROOT / "aircraft_state.json"
AIRCRAFT_GATEWAY = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def json_response(self, status: int, doc: dict) -> None:
        body = json.dumps(doc, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.json_response(200, {"ok": True, "time": time.time()})
            return
        if parsed.path == "/api/state":
            if STATE_PATH.exists():
                self.json_response(200, json.loads(STATE_PATH.read_text()))
            else:
                self.json_response(200, {"online": False, "updated_at": 0, "telemetry": {}})
            return
        if parsed.path == "/api/aircraft":
            if AIRCRAFT_GATEWAY:
                self.json_response(200, AIRCRAFT_GATEWAY.snapshot())
            else:
                self.json_response(503, {"ok": False, "error": "aircraft gateway not started"})
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/command":
            self.json_response(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            doc = json.loads(self.rfile.read(length).decode() or "{}")
            command = RoverCommand.from_dict(doc, source="ground_station")
        except Exception as exc:
            self.json_response(400, {"ok": False, "error": str(exc)})
            return
        with COMMAND_PATH.open("a") as f:
            f.write(json.dumps(command.__dict__, separators=(",", ":")) + "\n")
        self.json_response(200, {"ok": True, "command": command.__dict__})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rover ground station web server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--aircraft-udp-port", type=int, action="append", default=[14560, 14550])
    args = parser.parse_args(argv)
    global AIRCRAFT_GATEWAY
    AIRCRAFT_GATEWAY = start_aircraft_gateway(args.aircraft_udp_port, AIRCRAFT_STATE_PATH)
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ground station listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
