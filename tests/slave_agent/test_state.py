import unittest


class NoOpOpticalGateTests(unittest.TestCase):
    def test_gate_defaults_to_unblocked_and_is_independent_from_offline(self):
        from slave_agent.optical_gate import NoOpOpticalGate

        gate = NoOpOpticalGate()

        self.assertFalse(gate.snapshot()["blocked"])
        self.assertEqual(gate.snapshot()["state"], "CLEAR")
        self.assertTrue(gate.allows_commands())


class SlaveStateAggregatorTests(unittest.TestCase):
    def test_status_uses_the_injected_optical_gate(self):
        from slave_agent.state import SlaveStateAggregator

        class Gate:
            def snapshot(self):
                return {"blocked": True, "state": "BLOCKED", "reason": "reserved"}

        state = SlaveStateAggregator(clock=lambda: 100.0, optical_gate=Gate())
        state.update({"type": "HEARTBEAT", "mode": "GUIDED", "base_mode": 0})

        snapshot = state.snapshot()

        self.assertTrue(snapshot["blocked"])
        self.assertEqual(snapshot["link_state"], "OPTICAL_BLOCKED")

    def test_aggregates_real_flight_controller_telemetry(self):
        from slave_agent.state import SlaveStateAggregator

        now = [100.0]
        state = SlaveStateAggregator(clock=lambda: now[0], heartbeat_freshness=3.0)
        state.update({"type": "HEARTBEAT", "mode": "GUIDED", "base_mode": 128})
        state.update({
            "type": "GLOBAL_POSITION_INT",
            "lat": 321195600,
            "lon": 1189584060,
            "relative_alt": 12340,
            "vx": 300,
            "vy": 400,
            "hdg": 22900,
        })
        state.update({"type": "BATTERY_STATUS", "battery_remaining": 87})
        state.record_event("COMMAND", "GUIDED accepted", sequence=7)

        snapshot = state.snapshot()

        self.assertTrue(snapshot["online"])
        self.assertTrue(snapshot["fc_connected"])
        self.assertFalse(snapshot["blocked"])
        self.assertEqual(snapshot["mode"], "GUIDED")
        self.assertTrue(snapshot["armed"])
        self.assertAlmostEqual(snapshot["lat"], 32.11956)
        self.assertAlmostEqual(snapshot["lon"], 118.958406)
        self.assertAlmostEqual(snapshot["altitude"], 12.34)
        self.assertAlmostEqual(snapshot["speed"], 5.0)
        self.assertAlmostEqual(snapshot["heading"], 229.0)
        self.assertEqual(snapshot["battery"], 87.0)
        self.assertEqual(snapshot["event"]["sequence"], 7)
        self.assertEqual(snapshot["link_state"], "ONLINE")

    def test_stale_heartbeat_marks_fc_disconnected_without_optical_block(self):
        from slave_agent.state import SlaveStateAggregator

        now = [100.0]
        state = SlaveStateAggregator(clock=lambda: now[0], heartbeat_freshness=3.0)
        state.update({"type": "HEARTBEAT", "mode": "LOITER", "base_mode": 0})
        now[0] = 103.1

        snapshot = state.snapshot()

        self.assertFalse(snapshot["online"])
        self.assertFalse(snapshot["fc_connected"])
        self.assertFalse(snapshot["blocked"])
        self.assertEqual(snapshot["link_state"], "OFFLINE")

    def test_safe_update_never_raises_into_the_single_reader(self):
        from slave_agent.state import SlaveStateAggregator

        state = SlaveStateAggregator(clock=lambda: 100.0)

        state.safe_update({"type": "HEARTBEAT", "base_mode": object()})

        self.assertIn("telemetry_update", state.snapshot()["fault"])

    def test_plain_command_stage_clears_previous_mission_id(self):
        from slave_agent.state import SlaveStateAggregator

        state = SlaveStateAggregator(clock=lambda: 100.0)
        state.record_stage({
            "stage": "FAILED", "command_id": "m", "sequence": 1,
            "mission_id": "old-mission",
        })
        state.record_stage({
            "stage": "QUEUED", "command_id": "c", "sequence": 2,
            "mission_id": "",
        })

        snapshot = state.snapshot()
        self.assertEqual(snapshot["mission_id"], "")
        self.assertEqual(snapshot["mission_stage"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
