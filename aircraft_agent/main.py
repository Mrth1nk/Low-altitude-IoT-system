"""Production entry point for the isolated aircraft link and mission service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import threading
import time

from .command_worker import AircraftCommandWorker
from .inbox import DurableInbox
from .link_server import AircraftLinkServer
from .mavlink_session import MavlinkSession
from .mission_worker import AircraftMissionWorker
from .optical_gate import OpticalBlocked, OpticalGate
from .state_store import AtomicJsonStore
from .telemetry import AircraftTelemetry, message_type


class OpticalStateReader:
    def __init__(self, path, *, max_age_s=0.75, clock=time.time):
        self.store = AtomicJsonStore(path, max_bytes=64 * 1024)
        self.max_age_s = float(max_age_s)
        self.clock = clock

    def read(self):
        try:
            state = self.store.load({})
            timestamp = float(state.get("timestamp", 0.0))
            locked = state.get("locked") is True
            blocked = state.get("blocked") is True
            fresh = (
                timestamp > 0
                and 0.0 <= float(self.clock()) - timestamp <= self.max_age_s
            )
            if not fresh:
                return {
                    "locked": False,
                    "blocked": True,
                    "timestamp": timestamp,
                    "reason": "optical_state_stale",
                }
            if not locked or blocked:
                return {
                    **state,
                    "locked": False,
                    "blocked": True,
                    "reason": state.get("last_error") or "optical_blocked",
                }
            return {**state, "locked": True, "blocked": False, "reason": ""}
        except Exception as exc:
            return {
                "locked": False,
                "blocked": True,
                "timestamp": 0.0,
                "reason": f"optical_state_invalid:{type(exc).__name__}",
            }

    def require_locked(self):
        state = self.read()
        if not state["locked"]:
            raise OpticalBlocked(state.get("reason", "optical_blocked"))
        return state


class GatedOperations:
    """Rechecks the optical path immediately before every FC mutation."""

    def __init__(self, operations, optical_reader):
        self.operations = operations
        self.optical_reader = optical_reader

    def execute(self, record, *, start_auto=False):
        self.optical_reader.require_locked()
        return self.operations.execute(record, start_auto=start_auto)

    def arm(self, value):
        self.optical_reader.require_locked()
        return self.operations.arm(value)

    def set_mode(self, mode):
        self.optical_reader.require_locked()
        return self.operations.set_mode(mode)


class GatedMavlinkSession:
    """Checks the optical state before every flight-controller write."""

    def __init__(self, session, optical_reader):
        self.session = session
        self.optical_reader = optical_reader

    def send(self, kind, **fields):
        self.optical_reader.require_locked()
        return self.session.send(kind, **fields)

    def __getattr__(self, name):
        return getattr(self.session, name)


class HealthSnapshotWriter:
    def __init__(self, store, *, clock=time.time):
        self.store = store
        self.clock = clock

    def write(
        self,
        *,
        command_id,
        queue_depth,
        fc_heartbeat_at,
        optical,
        last_error,
    ):
        self.store.save(
            {
                "timestamp": float(self.clock()),
                "command_id": str(command_id or ""),
                "queue_depth": int(queue_depth),
                "fc_heartbeat_at": float(fc_heartbeat_at or 0.0),
                "optical": dict(optical),
                "last_error": str(last_error or "")[:256],
            }
        )


class TransactionJournal:
    def __init__(self, path, *, max_bytes=1024 * 1024, backups=5):
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.backups = int(backups)
        if self.max_bytes < 128 or self.backups < 1:
            raise ValueError("invalid transaction journal limits")
        self._identities = set()
        if self.path.exists():
            for line in self.path.read_text(errors="replace").splitlines():
                try:
                    record = json.loads(line)
                    self._identities.add(self._identity(record))
                except (ValueError, TypeError):
                    continue

    def append(self, record):
        identity = self._identity(record)
        if identity in self._identities:
            return False
        raw = (
            json.dumps(
                dict(record),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size + len(raw) > self.max_bytes:
            self._rotate()
        with self.path.open("ab") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self._identities.add(identity)
        return True

    def _rotate(self):
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                os.replace(
                    source, self.path.with_name(f"{self.path.name}.{index + 1}")
                )
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    @staticmethod
    def _identity(record):
        return (str(record.get("command_id", "")), str(record.get("stage", "")))


class HeartbeatMonitor:
    def __init__(self, *, clock=time.time):
        self.clock = clock
        self.timestamp = 0.0
        self._lock = threading.Lock()

    def observe(self, message):
        if message_type(message) == "HEARTBEAT":
            with self._lock:
                self.timestamp = float(self.clock())

    def read(self):
        with self._lock:
            return self.timestamp


def _apply_gate(gate, state):
    if state.get("locked") is True and state.get("blocked") is False:
        if not gate.locked:
            gate.set_locked(timestamp=state.get("timestamp"))
    else:
        reason = state.get("reason") or "optical_blocked"
        if gate.locked or getattr(gate, "_reason", None) != reason:
            gate.set_blocked(
                reason,
                timestamp=state.get("timestamp") or time.time(),
            )


def run():
    from serial import Serial

    psk = os.environ.get("AIRCRAFT_LINK_PSK", "")
    if len(psk.encode("utf-8")) < 16:
        raise RuntimeError("AIRCRAFT_LINK_PSK is missing or too short")

    state_dir = Path(os.environ.get("AIRCRAFT_STATE_DIR", "/var/lib/low-altitude-iot"))
    runtime_dir = Path(os.environ.get("AIRCRAFT_RUNTIME_DIR", "/run/low-altitude-iot"))
    fc_path = str(Path(os.environ["AIRCRAFT_FC_BY_ID"]).resolve(strict=True))
    link_path = str(Path(os.environ["AIRCRAFT_LINK_BY_ID"]).resolve(strict=True))
    optical_path = runtime_dir / "optical-state.json"

    telemetry = AircraftTelemetry()
    session = MavlinkSession.open(
        fc_path,
        baud=int(os.environ.get("AIRCRAFT_FC_BAUD", "115200")),
        telemetry=telemetry,
    )
    heartbeat = HeartbeatMonitor()
    session.subscribe(heartbeat.observe)
    session.start_reader()

    optical_reader = OpticalStateReader(
        optical_path,
        max_age_s=float(os.environ.get("OPTICAL_STATE_MAX_AGE", "0.75")),
    )
    gate = OpticalGate()
    _apply_gate(gate, optical_reader.read())

    inbox = DurableInbox(AtomicJsonStore(state_dir / "inbox.json"))
    gated_session = GatedMavlinkSession(session, optical_reader)
    mission = AircraftMissionWorker(gated_session, telemetry)
    operations = GatedOperations(mission, optical_reader)
    worker = AircraftCommandWorker(inbox, operations, start_thread=True)
    stream = Serial(
        link_path,
        baudrate=int(os.environ.get("AIRCRAFT_LINK_BAUD", "115200")),
        timeout=0.05,
        write_timeout=0.25,
    )
    server = AircraftLinkServer(
        inbox,
        gate,
        psk=psk,
        auth_store=AtomicJsonStore(state_dir / "auth.json"),
        stream=stream,
    )
    health = HealthSnapshotWriter(
        AtomicJsonStore(runtime_dir / "aircraft-health.json", max_bytes=64 * 1024)
    )
    journal = TransactionJournal(state_dir / "transactions.jsonl")
    stop = threading.Event()
    last_error = ""
    next_health = 0.0

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    stream.write(server.challenge_datagram())
    try:
        while not stop.is_set():
            optical = optical_reader.read()
            _apply_gate(gate, optical)
            try:
                server.pump_once(snapshot=telemetry.snapshot())
                last_error = ""
            except Exception as exc:
                last_error = f"{type(exc).__name__}:{exc}"[:256]
                time.sleep(0.05)
            for result in inbox.results:
                journal.append(result)
            now = time.time()
            if now >= next_health:
                active = inbox.active or {}
                health.write(
                    command_id=active.get("command_id", ""),
                    queue_depth=inbox.queue_depth + (1 if inbox.active else 0),
                    fc_heartbeat_at=heartbeat.read(),
                    optical=optical,
                    last_error=last_error,
                )
                next_health = now + 0.5
    finally:
        worker.close()
        session.close()
        stream.close()


if __name__ == "__main__":
    run()
