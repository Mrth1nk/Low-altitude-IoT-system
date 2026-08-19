import time
import unittest
import uuid

from rdk_agent.command_router import CloudCommand
from shared_protocol.node_messages import (
    build_ack_message,
    build_status_message,
    decode_node_message,
    encode_node_message,
)


class FakeSocket:
    def __init__(self):
        self.bound = None
        self.blocking = None
        self.incoming = []
        self.sent = []

    def bind(self, address):
        self.bound = address

    def setblocking(self, value):
        self.blocking = value

    def recvfrom(self, _size):
        if not self.incoming:
            raise BlockingIOError
        return self.incoming.pop(0)

    def sendto(self, payload, peer):
        self.sent.append((payload, peer))
        return len(payload)

    def close(self):
        pass


def slave_status(sequence=1, timestamp=1000.0, mode="GUIDED", fault=""):
    return build_status_message(
        source="aircraft_2",
        target="rover",
        command_id=str(uuid.uuid4()),
        sequence=sequence,
        timestamp=timestamp,
        online=True,
        fc_connected=True,
        blocked=False,
        mode=mode,
        armed=False,
        heartbeat_at=timestamp,
        lat=32.11974,
        lon=118.95314,
        position_observed=True,
        altitude=5.25,
        speed=1.2,
        heading=181.5,
        battery=88,
        mission_stage="IDLE",
        mission_id="",
        fault=fault,
        link_state="ONLINE",
        event={"timestamp": timestamp, "sequence": 1, "type": "HEARTBEAT", "text": "ok"},
    )


class SlaveTransportTests(unittest.TestCase):
    def make_transport(self):
        from rdk_agent.slave_transport import SlaveTransport

        self.wall = [1000.0]
        self.mono = [50.0]
        self.sock = FakeSocket()
        transport = SlaveTransport(
            local_host="0.0.0.0",
            local_port=14610,
            peer=("192.168.4.3", 14620),
            sock=self.sock,
            wall_clock=lambda: self.wall[0],
            monotonic_clock=lambda: self.mono[0],
            retry_interval=0.5,
            status_timeout=3.0,
            max_attempts=2,
            response_history_limit=2,
        )
        return transport

    def command(self, action="guided", payload=None):
        return CloudCommand(
            uuid.uuid4(), self.wall[0], "aircraft_2", action, payload or {}
        )

    def mark_online(self, transport):
        self.sock.incoming.append((
            encode_node_message(slave_status()), ("192.168.4.3", 14620)
        ))
        transport.pump()

    def test_binds_nonblocking_fixed_port_and_sends_typed_command_to_exact_peer(self):
        transport = self.make_transport()
        self.mark_online(transport)

        result = transport.execute(self.command())

        self.assertEqual(self.sock.bound, ("0.0.0.0", 14610))
        self.assertFalse(self.sock.blocking)
        message = decode_node_message(self.sock.sent[0][0], expected_target="aircraft_2")
        self.assertEqual(message["source"], "rover")
        self.assertEqual(message["payload"]["action"], "guided")
        self.assertEqual(self.sock.sent[0][1], ("192.168.4.3", 14620))
        self.assertTrue(result["accepted"])

    def test_offline_slave_rejects_new_commands_without_sending(self):
        from rdk_agent.slave_transport import SlaveUnavailable

        transport = self.make_transport()

        with self.assertRaisesRegex(SlaveUnavailable, "offline"):
            transport.execute(self.command())

        self.assertEqual(self.sock.sent, [])

    def test_wrong_peer_and_wrong_identity_cannot_refresh_state(self):
        transport = self.make_transport()
        wire = encode_node_message(slave_status())
        self.sock.incoming.extend([
            (wire, ("192.168.4.9", 14620)),
            (wire, ("192.168.4.3", 14621)),
        ])

        transport.pump()

        self.assertFalse(transport.snapshot()["online"])
        self.assertEqual(transport.diagnostics()["rx_wrong_peer"], 2)

    def test_status_becomes_offline_after_three_seconds_without_losing_last_values(self):
        transport = self.make_transport()
        self.sock.incoming.append((
            encode_node_message(slave_status()), ("192.168.4.3", 14620)
        ))
        transport.pump()
        self.assertTrue(transport.snapshot()["online"])

        self.mono[0] += 3.01
        state = transport.snapshot()

        self.assertFalse(state["online"])
        self.assertEqual(state["link_state"], "OFFLINE")
        self.assertEqual(state["mode"], "GUIDED")
        self.assertEqual(state["lat"], 32.11974)

    def test_pending_command_retries_and_terminal_ack_is_deduplicated(self):
        transport = self.make_transport()
        self.mark_online(transport)
        command = self.command("loiter")
        transport.execute(command)
        first = decode_node_message(self.sock.sent[-1][0])
        self.mono[0] += 0.51

        transport.pump()

        self.assertEqual(len(self.sock.sent), 2)
        self.assertEqual(self.sock.sent[0][0], self.sock.sent[1][0])
        ack = build_ack_message(
            source="aircraft_2", target="rover",
            command_id=str(command.command_id), sequence=first["sequence"],
            timestamp=self.wall[0], stage="VERIFIED", detail="mode confirmed",
        )
        wire = encode_node_message(ack)
        self.sock.incoming.extend([
            (wire, ("192.168.4.3", 14620)),
            (wire, ("192.168.4.3", 14620)),
        ])
        transport.pump()

        state = transport.snapshot()
        self.assertEqual(state["mission_stage"], "VERIFIED")
        self.assertEqual(state["event"]["sequence"], first["sequence"])
        self.assertEqual(transport.diagnostics()["rx_duplicates"], 1)
        sent = len(self.sock.sent)
        self.mono[0] += 1
        transport.pump()
        self.assertEqual(len(self.sock.sent), sent)

    def test_retry_budget_is_finite_and_reports_failed(self):
        transport = self.make_transport()
        self.mark_online(transport)
        transport.execute(self.command("guided"))
        self.mono[0] += 0.51
        transport.pump()
        self.mono[0] += 0.51

        transport.pump()

        self.assertEqual(len(self.sock.sent), 2)
        self.assertEqual(transport.diagnostics()["pending"], 0)
        self.assertEqual(transport.snapshot()["mission_stage"], "FAILED")
        self.assertIn("retry", transport.snapshot()["fault"])

    def test_link_expiry_cancels_pending_and_recovery_does_not_replay_it(self):
        transport = self.make_transport()
        self.mark_online(transport)
        transport.execute(self.command("land"))
        sent = len(self.sock.sent)
        self.mono[0] += 3.01

        transport.pump()
        self.sock.incoming.append((
            encode_node_message(slave_status(sequence=2, timestamp=1003.1)),
            ("192.168.4.3", 14620),
        ))
        transport.pump()

        self.assertEqual(len(self.sock.sent), sent)
        self.assertEqual(transport.diagnostics()["pending"], 0)
        self.assertTrue(transport.snapshot()["online"])
        self.assertEqual(transport.snapshot()["mission_stage"], "IDLE")

    def test_mission_is_transmitted_as_begin_items_commit_with_one_transaction_id(self):
        transport = self.make_transport()
        self.mark_online(transport)
        command = self.command("mission", {
            "mission_id": "route-1",
            "items": [
                {"lat": 32.1, "lng": 118.9, "alt": 12},
                {"lat": 32.2, "lng": 119.0, "alt": 15},
            ],
        })

        transport.execute(command)

        messages = [decode_node_message(wire) for wire, _peer in self.sock.sent]
        self.assertEqual(
            [message["type"] for message in messages],
            ["mission_begin", "mission_item", "mission_item", "mission_commit"],
        )
        self.assertEqual({message["command_id"] for message in messages}, {str(command.command_id)})
        self.assertEqual(messages[1]["payload"]["item"]["lon"], 118.9)
        self.assertEqual(messages[-1]["payload"]["digest"], messages[0]["payload"]["digest"])

    def test_response_dedup_history_is_bounded_and_unknown_ack_cannot_change_state(self):
        transport = self.make_transport()
        self.mark_online(transport)
        original = transport.snapshot()["mission_stage"]
        unknown = build_ack_message(
            source="aircraft_2", target="rover", command_id=str(uuid.uuid4()),
            sequence=999, timestamp=self.wall[0], stage="VERIFIED",
        )
        self.sock.incoming.append((encode_node_message(unknown), ("192.168.4.3", 14620)))
        transport.pump()
        self.assertEqual(transport.snapshot()["mission_stage"], original)

        for action in ("guided", "loiter", "land"):
            command = self.command(action)
            transport.execute(command)
            sent = decode_node_message(self.sock.sent[-1][0])
            ack = build_ack_message(
                source="aircraft_2", target="rover", command_id=str(command.command_id),
                sequence=sent["sequence"], timestamp=self.wall[0], stage="VERIFIED",
            )
            self.sock.incoming.append((encode_node_message(ack), ("192.168.4.3", 14620)))
            transport.pump()

        self.assertLessEqual(transport.diagnostics()["response_history"], 2)


if __name__ == "__main__":
    unittest.main()
