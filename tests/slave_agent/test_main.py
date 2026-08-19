import tempfile
import unittest
from pathlib import Path


class Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.connection = Connection()
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


class SlaveRuntimeTests(unittest.TestCase):
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
                state_dir=Path(tmp),
                runtime_dir=Path(tmp),
            )
            runtime = build_runtime(
                config,
                session_factory=open_session,
                link_factory=FakeLink,
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

            runtime.close()

            self.assertTrue(session.closed)
            self.assertTrue(session.connection.closed)
            self.assertTrue(runtime.link.closed)

    def test_config_rejects_non_by_id_flight_controller_path(self):
        from slave_agent.main import SlaveConfig

        with self.assertRaisesRegex(ValueError, "by-id"):
            SlaveConfig(
                fc_device="/dev/ttyACM0",
                fc_baud=115200,
                peer_ip="192.168.4.2",
                peer_port=14610,
                local_port=14620,
                state_dir=Path("/tmp/state"),
                runtime_dir=Path("/tmp/run"),
            )


if __name__ == "__main__":
    unittest.main()
