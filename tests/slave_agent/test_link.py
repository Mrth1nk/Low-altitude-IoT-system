import json
import threading
import time
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


class FailingSendSocket(FakeSocket):
    def sendto(self, payload, address):
        raise OSError("radio unavailable")


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

    def mark_delivered(self, result):
        pass


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

    def test_intake_capacity_must_be_bounded_and_positive(self):
        from slave_agent.link import SlaveUdpLink

        with self.assertRaisesRegex(ValueError, "intake_capacity"):
            SlaveUdpLink(
                Queue(), State(), peer_ip="192.168.4.2", peer_port=14610,
                local_port=14620, sock=FakeSocket(), intake_capacity=0,
            )

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
        self.assertTrue(link.wait_for_intake(timeout=1.0))

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
        self.assertTrue(link.wait_for_intake(timeout=1.0))

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

    def test_receive_loop_does_not_wait_for_durable_accept(self):
        class BlockingQueue(Queue):
            def __init__(self):
                super().__init__()
                self.started = threading.Event()
                self.release = threading.Event()

            def accept(self, message):
                self.started.set()
                self.release.wait(1.0)
                return super().accept(message)

        queue = BlockingQueue()
        link, sock, _now = self.make_link(queue)
        sock.incoming.append((encode_node_message(command()), ("192.168.4.2", 14610)))

        started_at = time.monotonic()
        link.receive_available()

        self.assertLess(time.monotonic() - started_at, 0.2)
        self.assertTrue(queue.started.wait(1.0))
        self.assertEqual(sock.sent, [])
        queue.release.set()
        self.assertTrue(link.wait_for_intake(timeout=1.0))
        self.assertEqual(decode_node_message(sock.sent[0][0])["type"], "ack")
        link.close()

    def test_bounded_intake_rejects_excess_without_persisting_it(self):
        class BlockingQueue(Queue):
            def __init__(self):
                super().__init__()
                self.started = threading.Event()
                self.release = threading.Event()

            def accept(self, message):
                self.started.set()
                self.release.wait(1.0)
                return super().accept(message)

        from slave_agent.link import SlaveUdpLink

        queue = BlockingQueue()
        sock = FakeSocket()
        link = SlaveUdpLink(
            queue, State(), peer_ip="192.168.4.2", peer_port=14610,
            local_port=14620, sock=sock, clock=lambda: 100.0,
            intake_capacity=1,
        )
        messages = [command() for _ in range(3)]
        sock.incoming.extend(
            (encode_node_message(message), ("192.168.4.2", 14610))
            for message in messages
        )

        link.receive_available()

        self.assertTrue(queue.started.wait(1.0))
        replies = [decode_node_message(payload) for payload, _peer in sock.sent]
        self.assertGreaterEqual(len(replies), 1)
        self.assertTrue(all(reply["type"] == "nack" for reply in replies))
        self.assertTrue(all(
            reply["payload"]["reason"] == "intake_full" for reply in replies
        ))
        queue.release.set()
        self.assertTrue(link.wait_for_intake(timeout=1.0))
        link.close()

    def test_recoverable_malformed_fixed_peer_packet_gets_correlated_nack(self):
        link, sock, _now = self.make_link()
        message = command()
        message["payload"] = {"action": 7, "parameters": {}}
        sock.incoming.append((
            json.dumps(message).encode("utf-8"),
            ("192.168.4.2", 14610),
        ))

        link.receive_available()

        reply = decode_node_message(sock.sent[0][0], expected_target="rover")
        self.assertEqual(reply["type"], "nack")
        self.assertEqual(reply["command_id"], message["command_id"])
        self.assertEqual(reply["sequence"], message["sequence"])
        self.assertEqual(reply["payload"]["reason"], "malformed_message")

    def test_terminal_ack_is_marked_delivered_only_after_successful_send(self):
        class CompletedQueue(Queue):
            def __init__(self):
                super().__init__()
                self.result = {
                    "command_id": str(uuid.uuid4()), "sequence": 23,
                    "stage": "VERIFIED", "detail": "verified",
                }
                self.marked = []

            def completed(self):
                return [self.result]

            def mark_delivered(self, result):
                self.marked.append(result)

        from slave_agent.link import SlaveUdpLink

        queue = CompletedQueue()
        sock = FakeSocket()
        link = SlaveUdpLink(
            queue, State(), peer_ip="192.168.4.2", peer_port=14610,
            local_port=14620, sock=sock, clock=lambda: 100.0,
        )
        link.publish_due()
        self.assertEqual(queue.marked, [queue.result])

        failed_queue = CompletedQueue()
        failed_link = SlaveUdpLink(
            failed_queue, State(), peer_ip="192.168.4.2", peer_port=14610,
            local_port=14620, sock=FailingSendSocket(), clock=lambda: 100.0,
        )
        with self.assertRaisesRegex(OSError, "radio unavailable"):
            failed_link.publish_due()
        self.assertEqual(failed_queue.marked, [])

    def test_persistence_failure_returns_nack_and_intake_worker_survives(self):
        class FailsOnceQueue(Queue):
            def __init__(self):
                super().__init__()
                self.remaining_failures = 1

            def accept(self, message):
                if self.remaining_failures:
                    self.remaining_failures -= 1
                    raise OSError("fsync failed")
                return super().accept(message)

        queue = FailsOnceQueue()
        link, sock, _now = self.make_link(queue)
        messages = [command(), command()]
        sock.incoming.extend(
            (encode_node_message(message), ("192.168.4.2", 14610))
            for message in messages
        )

        link.receive_available()
        self.assertTrue(link.wait_for_intake(timeout=1.0))

        replies = [decode_node_message(payload) for payload, _peer in sock.sent]
        self.assertEqual([reply["type"] for reply in replies], ["nack", "ack"])
        self.assertEqual(replies[0]["payload"]["reason"], "persistence_failed")
        link.close()


if __name__ == "__main__":
    unittest.main()
