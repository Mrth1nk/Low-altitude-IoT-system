import unittest
import uuid

from shared_protocol.node_messages import (
    build_command_message,
    decode_node_message,
    encode_node_message,
)


class FakeSocket:
    def __init__(self):
        self.incoming = []
        self.sent = []
        self.blocking = None
        self.bound = None

    def setblocking(self, value):
        self.blocking = value

    def bind(self, address):
        self.bound = address

    def recvfrom(self, _size):
        if not self.incoming:
            raise BlockingIOError
        return self.incoming.pop(0)

    def sendto(self, payload, address):
        self.sent.append((payload, address))
        return len(payload)

    def close(self):
        pass


class Queue:
    def __init__(self, error=None):
        self.messages = []
        self.error = error

    def accept(self, message):
        from slave_agent.command_queue import QueueAcceptance

        self.messages.append(message)
        if self.error:
            raise self.error
        return QueueAcceptance("QUEUED", False, message["sequence"])

    def completed(self):
        return []


class State:
    def __init__(self):
        self.events = []

    def record_event(self, event_type, text, **fields):
        self.events.append((event_type, text, fields))

    def snapshot(self):
        return {
            "online": True,
            "fc_connected": True,
            "blocked": False,
            "mode": "GUIDED",
            "armed": False,
            "heartbeat_at": 100.0,
            "lat": 32.1,
            "lon": 118.9,
            "position_observed": True,
            "altitude": 10.0,
            "speed": 1.0,
            "heading": 90.0,
            "battery": 80.0,
            "mission_stage": "IDLE",
            "mission_id": "",
            "fault": "",
            "link_state": "ONLINE",
            "event": {"timestamp": 100.0, "sequence": 0, "type": "NONE", "text": ""},
        }


def command(target="aircraft_2", timestamp=100.0):
    return build_command_message(
        action="guided",
        source="rover",
        target=target,
        command_id=str(uuid.uuid4()),
        sequence=9,
        timestamp=timestamp,
    )


class SlaveUdpLinkTests(unittest.TestCase):
    def make_link(self, queue=None, now=None):
        from slave_agent.link import SlaveUdpLink

        sock = FakeSocket()
        clock_value = now or [100.0]
        link = SlaveUdpLink(
            queue or Queue(),
            State(),
            peer_ip="192.168.4.2",
            peer_port=14610,
            local_port=14620,
            sock=sock,
            clock=lambda: clock_value[0],
        )
        return link, sock, clock_value

    def test_socket_is_nonblocking_and_bound_to_slave_port(self):
        _link, sock, _now = self.make_link()

        self.assertFalse(sock.blocking)
        self.assertEqual(sock.bound, ("0.0.0.0", 14620))

    def test_wrong_peer_is_rejected_with_sequence_echoing_nack(self):
        queue = Queue()
        link, sock, _now = self.make_link(queue)
        message = command()
        wire = encode_node_message(message)
        wrong_peer = ("192.168.4.9", 14610)
        sock.incoming.append((wire, wrong_peer))

        link.receive_available()

        self.assertEqual(queue.messages, [])
        reply = decode_node_message(sock.sent[0][0], expected_target="rover")
        self.assertEqual(reply["type"], "nack")
        self.assertEqual(reply["payload"]["reason"], "invalid_peer")
        self.assertEqual(reply["sequence"], message["sequence"])
        self.assertEqual(sock.sent[0][1], wrong_peer)

    def test_wrong_target_is_rejected_with_sequence_echoing_nack(self):
        queue = Queue()
        link, sock, _now = self.make_link(queue)
        message = command(target="aircraft_1")
        sock.incoming.append((
            encode_node_message(message),
            ("192.168.4.2", 14610),
        ))

        link.receive_available()

        self.assertEqual(queue.messages, [])
        reply = decode_node_message(sock.sent[0][0], expected_target="rover")
        self.assertEqual(reply["type"], "nack")
        self.assertEqual(reply["payload"]["reason"], "invalid_target")
        self.assertEqual(reply["sequence"], message["sequence"])

    def test_valid_command_returns_queued_ack_to_fixed_peer(self):
        queue = Queue()
        link, sock, _now = self.make_link(queue)
        message = command()
        sock.incoming.append((encode_node_message(message), ("192.168.4.2", 14610)))

        link.receive_available()

        reply = decode_node_message(sock.sent[0][0], expected_target="rover")
        self.assertEqual(len(queue.messages), 1)
        self.assertEqual(reply["type"], "ack")
        self.assertEqual(reply["payload"]["stage"], "QUEUED")
        self.assertEqual(reply["command_id"], message["command_id"])
        self.assertEqual(sock.sent[0][1], ("192.168.4.2", 14610))

    def test_queue_rejection_returns_failed_nack(self):
        from slave_agent.command_queue import CommandRejected

        link, sock, _now = self.make_link(Queue(CommandRejected("expired")))
        sock.incoming.append((encode_node_message(command()), ("192.168.4.2", 14610)))

        link.receive_available()

        reply = decode_node_message(sock.sent[0][0], expected_target="rover")
        self.assertEqual(reply["type"], "nack")
        self.assertEqual(reply["payload"], {"reason": "expired", "stage": "FAILED"})

    def test_status_is_sent_at_one_hz_and_peer_freshness_is_separate(self):
        link, sock, now = self.make_link()

        link.publish_due()
        now[0] = 100.5
        link.publish_due()
        now[0] = 101.0
        link.publish_due()
        now[0] = 104.1

        messages = [decode_node_message(payload) for payload, _peer in sock.sent]
        self.assertEqual([message["type"] for message in messages], ["status", "status"])
        self.assertFalse(link.peer_online(max_age=3.0))
        self.assertFalse(messages[-1]["payload"]["blocked"])

    def test_completed_worker_result_emits_final_ack_and_updates_event(self):
        class CompletedQueue(Queue):
            def completed(self):
                return [{
                    "command_id": str(uuid.uuid4()),
                    "sequence": 23,
                    "stage": "VERIFIED",
                    "detail": "mission readback verified",
                }]

        from slave_agent.link import SlaveUdpLink

        sock = FakeSocket()
        state = State()
        link = SlaveUdpLink(
            CompletedQueue(), state,
            peer_ip="192.168.4.2", peer_port=14610, local_port=14620,
            sock=sock, clock=lambda: 100.0,
        )

        link.publish_due()

        messages = [decode_node_message(payload) for payload, _peer in sock.sent]
        self.assertEqual(messages[0]["type"], "ack")
        self.assertEqual(messages[0]["payload"]["stage"], "VERIFIED")
        self.assertEqual(state.events[0][0], "MISSION")
        self.assertEqual(state.events[0][2]["stage"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
