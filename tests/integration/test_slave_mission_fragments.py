import tempfile
import unittest
import uuid
from pathlib import Path

from aircraft_agent.inbox import DurableInbox
from aircraft_agent.state_store import AtomicJsonStore
from rdk_agent.command_router import CloudCommand, CommandRouter
from rdk_agent.slave_transport import SlaveTransport
from shared_protocol.node_messages import (
    build_status_message,
    decode_node_message,
    encode_node_message,
    mission_digest,
)
from slave_agent.command_queue import NodeCommandQueue, ThreadSafeInbox


class FakeSocket:
    def __init__(self):
        self.incoming = []
        self.sent = []

    def bind(self, _address):
        pass

    def setblocking(self, _value):
        pass

    def recvfrom(self, _size):
        if not self.incoming:
            raise BlockingIOError
        return self.incoming.pop(0)

    def sendto(self, payload, peer):
        self.sent.append((payload, peer))
        return len(payload)

    def close(self):
        pass


class RecordingExecutor:
    def __init__(self):
        self.commands = []

    def execute(self, command):
        self.commands.append(command)
        return {"accepted": True, "stage": "routed"}


class SlaveMissionFragmentIntegrationTests(unittest.TestCase):
    def test_ground_fragments_are_reassembled_into_one_typed_slave_mission(self):
        now = 1_800_000_000.0
        command_id = uuid.UUID("11223344-5566-7788-99aa-bbccddeeff22")
        mission_id = "slave-route-1"
        items = [
            {
                "mission_id": mission_id,
                "index": 0,
                "lat": 32.1197,
                "lon": 118.9531,
                "alt": 20.0,
                "command": 16,
                "frame": 6,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "autocontinue": True,
            },
            {
                "mission_id": mission_id,
                "index": 1,
                "lat": 32.1200,
                "lon": 118.9540,
                "alt": 22.0,
                "command": 16,
                "frame": 6,
                "param1": 0.0,
                "param2": 0.0,
                "param3": 0.0,
                "param4": 0.0,
                "autocontinue": True,
            },
        ]
        checksum = mission_digest(items)
        fragments = [
            {
                "command": "aircraft_mission_begin",
                "command_id": str(command_id),
                "target": "aircraft_2",
                "source_timestamp": now,
                "payload": {
                    "mission_id": mission_id,
                    "item_count": len(items),
                    "vehicle": "aircraft_2",
                    "checksum": checksum,
                },
            },
            *[
                {
                    "command": "aircraft_mission_item",
                    "command_id": str(command_id),
                    "target": "aircraft_2",
                    "source_timestamp": now,
                    "payload": item,
                }
                for item in items
            ],
            {
                "command": "aircraft_mission_commit",
                "command_id": str(command_id),
                "target": "aircraft_2",
                "source_timestamp": now,
                "payload": {
                    "mission_id": mission_id,
                    "item_count": len(items),
                    "checksum": checksum,
                },
            },
        ]

        sock = FakeSocket()
        transport = SlaveTransport(
            local_port=14610,
            peer=("192.168.4.3", 14620),
            sock=sock,
            wall_clock=lambda: now,
            monotonic_clock=lambda: 50.0,
        )
        status = build_status_message(
            source="aircraft_2",
            target="rover",
            command_id=str(uuid.uuid4()),
            sequence=1,
            timestamp=now,
            online=True,
            fc_connected=True,
            blocked=False,
            mode="GUIDED",
            armed=False,
            heartbeat_at=now,
            lat=0.0,
            lon=0.0,
            position_observed=False,
            altitude=0.0,
            speed=0.0,
            heading=0.0,
            battery=100.0,
            mission_stage="IDLE",
            mission_id="",
            fault="",
            link_state="ONLINE",
            event={"timestamp": now, "sequence": 1, "type": "HEARTBEAT", "text": "ok"},
        )
        sock.incoming.append((encode_node_message(status), ("192.168.4.3", 14620)))
        transport.pump()
        main = RecordingExecutor()
        router = CommandRouter(
            rover_executor=RecordingExecutor(),
            aircraft_link=main,
            optical_state=lambda: "locked",
            aircraft_2_link=transport,
            clock=lambda: now,
        )

        for index, raw in enumerate(fragments):
            result = router.route(CloudCommand.from_cloud(raw, clock=lambda: now))
            if index < len(fragments) - 1:
                self.assertEqual(sock.sent, [])
                self.assertEqual(result["stage"], "RECEIVED")

        messages = [decode_node_message(wire) for wire, _peer in sock.sent]
        self.assertEqual(
            [message["type"] for message in messages],
            ["mission_begin", "mission_item", "mission_item", "mission_commit"],
        )
        self.assertEqual({message["command_id"] for message in messages}, {str(command_id)})
        self.assertEqual(messages[0]["payload"]["digest"], messages[-1]["payload"]["digest"])
        self.assertNotEqual(messages[0]["payload"]["digest"], checksum)
        self.assertEqual(main.commands, [])

        with tempfile.TemporaryDirectory() as tmp:
            inbox = ThreadSafeInbox(DurableInbox(AtomicJsonStore(Path(tmp) / "inbox.json")))
            queue = NodeCommandQueue(inbox, clock=lambda: now)
            for message in messages:
                queue.accept(message)
            self.assertEqual(inbox.active["kind"], "mission")
            self.assertEqual(inbox.active["mission_id"], mission_id)
            self.assertEqual(len(inbox.active["items"]), 2)


if __name__ == "__main__":
    unittest.main()
