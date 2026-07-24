from __future__ import annotations

import queue
import threading

from .telemetry import message_type


class _Transaction:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self.session._begin_transaction()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.session._end_transaction()
        return False

    def receive(self, message_types, timeout):
        accepted = set(message_types)
        deadline = self.session.clock() + float(timeout)
        while True:
            remaining = deadline - self.session.clock()
            if remaining <= 0:
                return None
            try:
                message = self.session._transaction_queue.get(
                    timeout=remaining
                )
            except queue.Empty:
                return None
            if message_type(message) in accepted:
                return message


class MavlinkSession:
    """The only owner and reader of the aircraft flight-controller link."""

    def __init__(
        self,
        connection,
        *,
        telemetry,
        target_system=1,
        target_component=1,
        gcs_system=255,
        gcs_component=0,
        clock=None,
    ):
        import time

        self.connection = connection
        self.telemetry = telemetry
        self.target_system = int(target_system)
        self.target_component = int(target_component)
        self.gcs_system = int(gcs_system)
        self.gcs_component = int(gcs_component)
        self.clock = clock or time.monotonic
        self._transaction_lock = threading.Lock()
        self._transaction_queue = None
        self._reader_ident = None
        self._reader_worker = None
        self._stop_reader = threading.Event()
        self._reader_ids = set()
        self._subscribers = []

    @classmethod
    def open(cls, path="/dev/ttyACM0", baud=115200, **kwargs):
        from pymavlink import mavutil

        connection = mavutil.mavlink_connection(
            path, baud=int(baud), autoreconnect=True, source_system=255
        )
        connection.wait_heartbeat(timeout=5)
        return cls(
            connection,
            target_system=connection.target_system or 1,
            target_component=connection.target_component or 1,
            **kwargs,
        )

    @property
    def reader_count(self):
        return len(self._reader_ids)

    def transaction(self):
        return _Transaction(self)

    def subscribe(self, callback):
        if not callable(callback):
            raise TypeError("subscriber must be callable")
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def start_reader(self):
        if self._reader_worker is not None and self._reader_worker.is_alive():
            return
        self._stop_reader.clear()
        self._reader_worker = threading.Thread(
            target=self._reader_loop,
            name="aircraft-mavlink-reader",
            daemon=True,
        )
        self._reader_worker.start()

    def close(self):
        self._stop_reader.set()
        if self._reader_worker is not None:
            self._reader_worker.join(timeout=1)

    def _reader_loop(self):
        while not self._stop_reader.is_set():
            self.dispatch_once(timeout=0.1)

    def _begin_transaction(self):
        if not self._transaction_lock.acquire(blocking=False):
            raise RuntimeError("MAVLink transaction active")
        self._transaction_queue = queue.Queue()

    def _end_transaction(self):
        self._transaction_queue = None
        self._transaction_lock.release()

    def dispatch_once(self, timeout=0.1):
        reader_id = threading.get_ident()
        if self._reader_ident is None:
            self._reader_ident = reader_id
        elif self._reader_ident != reader_id:
            raise RuntimeError("MavlinkSession permits only one reader")
        self._reader_ids.add(reader_id)
        message = self.connection.recv_match(blocking=True, timeout=timeout)
        if message is None:
            return None
        self.telemetry.update(message)
        for callback in tuple(self._subscribers):
            callback(message)
        transaction_queue = self._transaction_queue
        if transaction_queue is not None:
            transaction_queue.put(message)
        return message

    def send(self, kind, **fields):
        mav = self.connection.mav
        target = (self.target_system, self.target_component)
        mission_type = int(fields.get("mission_type", 0))
        if kind == "MISSION_CLEAR_ALL":
            self._compat(mav.mission_clear_all_send, *target, mission_type)
        elif kind == "MISSION_COUNT":
            self._compat(
                mav.mission_count_send,
                *target,
                int(fields["count"]),
                mission_type,
            )
        elif kind == "MISSION_ITEM_INT":
            item = fields["item"]
            self._compat(
                mav.mission_item_int_send,
                *target,
                item["seq"],
                item["frame"],
                item["command"],
                0,
                item["autocontinue"],
                item["param1"],
                item["param2"],
                item["param3"],
                item["param4"],
                item["x"],
                item["y"],
                item["z"],
                mission_type,
            )
        elif kind == "MISSION_REQUEST_LIST":
            self._compat(mav.mission_request_list_send, *target, mission_type)
        elif kind == "MISSION_REQUEST_INT":
            self._compat(
                mav.mission_request_int_send,
                *target,
                int(fields["seq"]),
                mission_type,
            )
        elif kind == "MISSION_ACK":
            self._compat(
                mav.mission_ack_send,
                *target,
                int(fields["result"]),
                mission_type,
            )
        elif kind == "SET_MODE":
            mapping = self.connection.mode_mapping() or {}
            mode = str(fields["mode"]).upper()
            if mode not in mapping:
                raise RuntimeError(f"mode not available: {mode}")
            self.connection.set_mode(mapping[mode])
        elif kind == "ARM":
            self.connection.arducopter_arm() if fields["value"] else self.connection.arducopter_disarm()
        else:
            raise ValueError(f"unsupported MAVLink operation {kind}")

    @staticmethod
    def _compat(function, *args):
        try:
            return function(*args)
        except TypeError:
            return function(*args[:-1])
