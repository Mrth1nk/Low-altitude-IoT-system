import tempfile
import threading
import time
import unittest
import json
from pathlib import Path


class Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.connection = Connection()
        self.reader_error = None
        self.subscribers = []
        self.reader_starts = 0
        self.requests = []
        self.closed = False

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def start_reader(self):
        self.reader_starts += 1

    def request_message_interval(self, message_id, interval_us):
        self.requests.append((message_id, interval_us))

    def close(self):
        self.closed = True


class FakeLink:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    def run_once(self):
        return 0

    def peer_online(self, max_age=3.0):
        return False

    def close(self):
        self.closed = True


class FakeFollowReceiver:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.closed = False

    def run_once(self):
        return 0

    def snapshot(self):
        return {"received": 0}

    def close(self):
        self.closed = True


class SlaveRuntimeTests(unittest.TestCase):
    def test_runtime_exits_when_mavlink_reader_loses_the_serial_device(self):
        from slave_agent.main import SlaveRuntime

        class BrokenSession(Session):
            reader_error = RuntimeError("serial disconnected")

            def __init__(self):
                super().__init__()
                self.reader_error = RuntimeError("serial disconnected")

        runtime = SlaveRuntime(
            config=None,
            session=BrokenSession(),
            worker=None,
            link=FakeLink(),
            inbox=None,
            state=None,
            health_store=None,
            clock=lambda: 0.0,
        )

        with self.assertRaisesRegex(RuntimeError, "MAVLink reader failed"):
            runtime.run_once()

    def test_build_runtime_uses_fixed_by_id_and_single_reader(self):
        from slave_agent.main import SlaveConfig, build_runtime

        with tempfile.TemporaryDirectory() as tmp:
            session = Session()
            calls = []

            def open_session(path, baud, **kwargs):
                calls.append((path, baud, kwargs))
                return session

            config = SlaveConfig(
                fc_device="/dev/serial/by-id/usb-ArduPilot_MengChuang-if00",
                fc_baud=115200,
                peer_ip="192.168.4.2",
                peer_port=14610,
                local_port=14620,
                follow_port=14630,
                state_dir=Path(tmp),
                runtime_dir=Path(tmp),
            )
            runtime = build_runtime(
                config,
                session_factory=open_session,
                link_factory=FakeLink,
                follow_receiver_factory=FakeFollowReceiver,
                start_worker=False,
            )

            self.assertEqual(calls[0][0], config.fc_device)
            self.assertEqual(calls[0][1], 115200)
            self.assertEqual(session.reader_starts, 1)
            self.assertEqual(len(session.subscribers), 1)
            self.assertIn((0, 1_000_000), session.requests)
            self.assertIn((33, 1_000_000), session.requests)
            self.assertEqual(runtime.link.kwargs["local_port"], 14620)
            self.assertEqual(runtime.link.kwargs["peer_port"], 14610)
            self.assertEqual(runtime.follow_receiver.kwargs["local_port"], 14630)
            self.assertIs(runtime.follow_receiver.args[0], session)
            from slave_agent.command_queue import VerifiedOperations
            from slave_agent.main import NetworkRequestOperations
            from slave_agent.optical_gate import NoOpOpticalGate

            command_queue = runtime.link.args[0]
            self.assertIsInstance(runtime.worker.operations, NetworkRequestOperations)
            self.assertIsInstance(runtime.worker.operations.delegate, VerifiedOperations)
            self.assertIsInstance(command_queue.optical_gate, NoOpOpticalGate)
            self.assertIs(command_queue.optical_gate, runtime.state.optical_gate)
            self.assertIs(command_queue.stage_callback.__self__, runtime.state)
            self.assertIs(
                command_queue.stage_callback.__func__,
                runtime.state.record_stage.__func__,
            )
            self.assertIsNotNone(command_queue.delivery_store)

            runtime.close()

            self.assertTrue(session.closed)
            self.assertTrue(session.connection.closed)
            self.assertTrue(runtime.link.closed)
            self.assertTrue(runtime.follow_receiver.closed)

    def test_network_phone_request_is_persisted_without_touching_flight_mode(self):
        from slave_agent.main import NetworkRequestOperations

        class Delegate:
            def __init__(self):
                self.modes = []

            def set_mode(self, mode):
                self.modes.append(mode)

        with tempfile.TemporaryDirectory() as tmp:
            delegate = Delegate()
            path = Path(tmp) / "network-mode"
            operations = NetworkRequestOperations(
                delegate, path, clock=lambda: 123.5
            )

            operations.set_mode("NETWORK_PHONE")

            self.assertEqual(delegate.modes, [])
            self.assertEqual(json.loads(path.read_text()), {
                "mode": "phone",
                "requested_at": 123.5,
                "source": "aircraft_2",
            })

    def test_close_waits_for_active_worker_before_closing_serial(self):
        from slave_agent.main import SlaveRuntime

        release = threading.Event()

        class Worker:
            def __init__(self):
                self.closed = False
                self._thread = threading.Thread(target=release.wait, daemon=True)
                self._thread.start()

            def close(self):
                self.closed = True

        class Link:
            def close(self):
                pass

        class Store:
            def save(self, _value):
                pass

        worker = Worker()
        session = Session()
        runtime = SlaveRuntime(
            config=None, session=session, worker=worker, link=Link(), inbox=None,
            state=None, health_store=Store(),
        )
        closer = threading.Thread(target=runtime.close)
        closer.start()
        time.sleep(0.05)

        self.assertTrue(worker.closed)
        self.assertFalse(session.closed)
        self.assertFalse(session.connection.closed)

        release.set()
        closer.join(1.0)
        self.assertFalse(closer.is_alive())
        self.assertTrue(session.closed)
        self.assertTrue(session.connection.closed)

    def test_config_rejects_non_by_id_flight_controller_path(self):
        from slave_agent.main import SlaveConfig

        with self.assertRaisesRegex(ValueError, "by-id"):
            SlaveConfig(
                fc_device="/dev/ttyACM0",
                fc_baud=115200,
                peer_ip="192.168.4.2",
                peer_port=14610,
                local_port=14620,
                follow_port=14630,
                state_dir=Path("/tmp/state"),
                runtime_dir=Path("/tmp/run"),
            )


if __name__ == "__main__":
    unittest.main()
