import json
from pathlib import Path
import sys
import tempfile
import unittest


RDK_DIR = Path(__file__).resolve().parents[2] / "rdk_agent"
sys.path.insert(0, str(RDK_DIR))
import ground_station_server  # noqa: E402


class AircraftSnapshotTests(unittest.TestCase):
    def test_reads_agent_snapshot_without_starting_second_udp_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "aircraft.json"
            expected = {"online": True, "mode": "GUIDED"}
            state_path.write_text(json.dumps(expected))

            actual = ground_station_server.read_aircraft_snapshot(
                gateway=None,
                state_path=state_path,
            )

        self.assertEqual(actual, expected)

    def test_missing_snapshot_reports_waiting_state(self):
        actual = ground_station_server.read_aircraft_snapshot(
            gateway=None,
            state_path=Path("/definitely/missing/aircraft-state.json"),
        )
        self.assertEqual(actual["online"], False)
        self.assertEqual(actual["state"], "waiting")

    def test_default_cli_does_not_bind_aircraft_udp_ports(self):
        args = ground_station_server.parse_args([])
        self.assertEqual(args.aircraft_udp_port, [])


if __name__ == "__main__":
    unittest.main()
