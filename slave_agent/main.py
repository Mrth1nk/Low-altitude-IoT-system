from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import threading
import time

from aircraft_agent.command_worker import AircraftCommandWorker
from aircraft_agent.inbox import DurableInbox
from aircraft_agent.mavlink_session import MavlinkSession
from aircraft_agent.mission_worker import AircraftMissionWorker
from aircraft_agent.state_store import AtomicJsonStore
from aircraft_agent.telemetry import AircraftTelemetry

from .command_queue import NodeCommandQueue, ThreadSafeInbox, VerifiedOperations
from .link import SlaveUdpLink
from .optical_gate import NoOpOpticalGate
from .state import SlaveStateAggregator


@dataclass(frozen=True)
class SlaveConfig:
    fc_device: str
    fc_baud: int
    peer_ip: str
    peer_port: int
    local_port: int
    state_dir: Path
    runtime_dir: Path

    def __post_init__(self):
        if not str(self.fc_device).startswith("/dev/serial/by-id/"):
            raise ValueError("flight controller must use a fixed /dev/serial/by-id path")
        if int(self.fc_baud) <= 0:
            raise ValueError("flight-controller baud must be positive")
        for value in (self.peer_port, self.local_port):
            if not 1 <= int(value) <= 65535:
                raise ValueError("UDP port must be from 1 to 65535")

    @classmethod
    def from_env(cls):
        return cls(
            fc_device=os.environ["SLAVE_FC_DEVICE"],
            fc_baud=int(os.environ.get("SLAVE_FC_BAUD", "115200")),
            peer_ip=os.environ.get("SLAVE_ROVER_IP", "192.168.4.2"),
            peer_port=int(os.environ.get("SLAVE_ROVER_PORT", "14610")),
            local_port=int(os.environ.get("SLAVE_LOCAL_PORT", "14620")),
            state_dir=Path(os.environ.get("SLAVE_STATE_DIR", "/var/lib/low-altitude-slave")),
            runtime_dir=Path(os.environ.get("SLAVE_RUNTIME_DIR", "/run/low-altitude-slave")),
        )


class NetworkRequestOperations:
    """Keeps maintenance network changes out of the MAVLink command path."""

    def __init__(self, delegate, request_path, *, clock=time.time):
        self.delegate = delegate
        self.request_store = AtomicJsonStore(request_path, max_bytes=4096)
        self.clock = clock

    def set_mode(self, mode):
        if str(mode).upper() != "NETWORK_PHONE":
            return self.delegate.set_mode(mode)
        self.request_store.save({
            "mode": "phone",
            "requested_at": float(self.clock()),
            "source": "aircraft_2",
        })
        return None

    def arm(self, value):
        return self.delegate.arm(value)

    def execute(self, record, start_auto=False):
        return self.delegate.execute(record, start_auto=start_auto)


class SlaveRuntime:
    def __init__(self, *, config, session, worker, link, inbox, state, health_store, clock=time.time):
        self.config = config
        self.session = session
        self.worker = worker
        self.link = link
        self.inbox = inbox
        self.state = state
        self.health_store = health_store
        self.clock = clock
        self._last_health_at = 0.0
        self._closed = False

    def run_once(self):
        received = self.link.run_once()
        now = float(self.clock())
        if now - self._last_health_at >= 1.0:
            self._last_health_at = now
            snapshot = self.state.snapshot()
            self.health_store.save({
                "timestamp": now,
                "fc_heartbeat_at": snapshot["heartbeat_at"],
                "fc_connected": snapshot["fc_connected"],
                "udp_peer_online": self.link.peer_online(max_age=3.0),
                "queue_depth": self.inbox.queue_depth,
                "blocked": snapshot["blocked"],
                "last_error": snapshot["fault"],
            })
        return received

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.link.close()
        self.worker.close()
        worker_thread = getattr(self.worker, "_thread", None)
        if worker_thread is not None and worker_thread.is_alive():
            worker_thread.join()
        self.session.close()
        connection = getattr(self.session, "connection", None)
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def build_runtime(
    config,
    *,
    session_factory=MavlinkSession.open,
    link_factory=SlaveUdpLink,
    start_worker=True,
    clock=time.time,
):
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    telemetry = AircraftTelemetry(freshness=3.0)
    optical_gate = NoOpOpticalGate()
    state = SlaveStateAggregator(
        clock=clock,
        heartbeat_freshness=3.0,
        optical_gate=optical_gate,
    )
    session = session_factory(
        config.fc_device,
        config.fc_baud,
        telemetry=telemetry,
    )
    session.subscribe(state.safe_update)
    session.start_reader()
    for message_id, interval_us in ((0, 1_000_000), (33, 1_000_000), (147, 1_000_000)):
        session.request_message_interval(message_id, interval_us)

    inbox = ThreadSafeInbox(DurableInbox(
        AtomicJsonStore(config.state_dir / "inbox.json"),
        max_queue=16,
    ))
    mission = AircraftMissionWorker(session, telemetry)
    verified_operations = VerifiedOperations(mission, state)
    operations = NetworkRequestOperations(
        verified_operations,
        config.runtime_dir / "network-mode",
        clock=clock,
    )
    worker = AircraftCommandWorker(
        inbox,
        operations,
        max_pending=16,
        start_thread=start_worker,
    )
    command_queue = NodeCommandQueue(
        inbox,
        clock=clock,
        max_age=3.0,
        delivery_store=AtomicJsonStore(config.state_dir / "delivery.json"),
        stage_callback=state.record_stage,
        optical_gate=optical_gate,
    )
    link = link_factory(
        command_queue,
        state,
        peer_ip=config.peer_ip,
        peer_port=config.peer_port,
        local_port=config.local_port,
        clock=clock,
    )
    return SlaveRuntime(
        config=config,
        session=session,
        worker=worker,
        link=link,
        inbox=inbox,
        state=state,
        health_store=AtomicJsonStore(config.runtime_dir / "slave-health.json"),
        clock=clock,
    )


def main():
    runtime = build_runtime(SlaveConfig.from_env())
    stop = threading.Event()

    def request_stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop.is_set():
            runtime.run_once()
            stop.wait(0.01)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
