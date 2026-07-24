from __future__ import annotations

from collections import deque
import threading

from .mission_worker import MissionUnsafeResidual


class AircraftCommandWorker:
    """Serializes every flight-controller mutation through one FIFO."""

    def __init__(
        self,
        inbox,
        operations,
        *,
        max_pending=16,
        start_thread=False,
    ):
        if int(max_pending) < 1:
            raise ValueError("max_pending must be positive")
        self.inbox = inbox
        self.operations = operations
        self.max_pending = int(max_pending)
        self._queue = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._closed = False
        self._thread = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._run, name="aircraft-command-worker", daemon=True
            )
            self._thread.start()

    def submit(self, record):
        with self._lock:
            if self._closed:
                raise RuntimeError("command worker is closed")
            if len(self._queue) >= self.max_pending:
                raise RuntimeError("command worker queue is full")
            self._queue.append(dict(record))
            self._wake.set()

    def run_once(self):
        record = self.inbox.active if self.inbox is not None else self._pop()
        if record is None:
            return None
        try:
            result = self._execute(record)
            stage = "VERIFIED" if record["kind"] == "mission" else "COMPLETED"
            detail = str(getattr(result, "reason", "") or "")
            extra = {
                "verified": bool(getattr(result, "verified", True)),
                "execution_ready": bool(
                    getattr(result, "execution_ready", True)
                ),
            }
        except MissionUnsafeResidual as exc:
            stage, detail, extra = (
                "UNSAFE_RESIDUAL",
                str(exc),
                {"verified": False, "execution_ready": False},
            )
        except Exception as exc:
            stage, detail, extra = (
                "FAILED",
                str(exc),
                {"verified": False, "execution_ready": False},
            )
        if self.inbox is not None:
            return self.inbox.finish_active(stage, detail, **extra)
        return {
            "command_id": record["command_id"],
            "stage": stage,
            "detail": detail,
            **extra,
        }

    def run_queued(self):
        results = []
        while True:
            result = self.run_once()
            if result is None:
                return results
            results.append(result)

    def close(self):
        self._closed = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def _run(self):
        while not self._closed:
            result = self.run_once()
            if result is None:
                self._wake.wait(0.25)
                self._wake.clear()

    def _pop(self):
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def _execute(self, record):
        if record["kind"] == "mission":
            return self.operations.execute(record, start_auto=True)
        action = str(record["action"]).lower()
        if action == "arm":
            return self.operations.arm(True)
        if action == "disarm":
            return self.operations.arm(False)
        self.operations.set_mode(action.upper())
        return None
