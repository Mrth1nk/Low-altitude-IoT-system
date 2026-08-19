import json
import unittest
import uuid

from shared_protocol.node_messages import (
    MAX_DATAGRAM_BYTES,
    NodeMessageError,
    build_ack_message,
    build_command_message,
    build_mission_begin_message,
    build_mission_commit_message,
    build_mission_item_message,
    build_nack_message,
    build_status_message,
    decode_node_message,
    encode_node_message,
    make_node_message,
    mission_digest,
)


class NodeMessageTests(unittest.TestCase):
    def setUp(self):
        self.command_id = "00112233-4455-6677-8899-aabbccddeeff"

    @staticmethod
    def status_payload():
        return {
            "online": True,
            "fc_connected": True,
            "blocked": False,
            "mode": "GUIDED",
            "armed": False,
            "heartbeat_at": 9.5,
            "lat": 32.1,
            "lon": 118.9,
            "position_observed": True,
            "altitude": 20.0,
            "speed": 0.0,
            "heading": 180.0,
            "battery": 100.0,
            "mission_stage": "IDLE",
            "mission_id": "",
            "fault": "",
            "link_state": "ONLINE",
            "event": {"timestamp": 9.0, "sequence": 3, "type": "HEARTBEAT", "text": "GUIDED"},
        }

    def test_round_trip_is_canonical_and_preserves_identity(self):
        message = make_node_message(
            "command",
            source="rover",
            target="aircraft_2",
            command_id=self.command_id,
            sequence=7,
            timestamp=1784890000.25,
            payload={"parameters": {}, "action": "guided"},
        )

        wire = encode_node_message(message)

        self.assertEqual(wire, json.dumps(
            message,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"))
        self.assertEqual(
            decode_node_message(wire, expected_target="aircraft_2"),
            message,
        )

    def test_rejects_missing_wrong_typed_and_unknown_fields(self):
        valid = make_node_message(
            "status",
            source="aircraft_2",
            target="rover",
            command_id=str(uuid.UUID(int=0)),
            sequence=1,
            timestamp=10.0,
            payload=self.status_payload(),
        )
        invalid = []
        for field in valid:
            candidate = dict(valid)
            candidate.pop(field)
            invalid.append(candidate)
        invalid.extend([
            dict(valid, version=True),
            dict(valid, type=[]),
            dict(valid, source=[]),
            dict(valid, target={}),
            dict(valid, source="unknown"),
            dict(valid, target="unknown"),
            dict(valid, command_id="not-a-uuid"),
            dict(valid, sequence=-1),
            dict(valid, timestamp=float("inf")),
            dict(valid, payload=[]),
            dict(valid, unexpected=True),
        ])

        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(NodeMessageError):
                decode_node_message(json.dumps(candidate).encode("utf-8"))

        for payload in (
            {**self.status_payload(), "event": []},
            {**self.status_payload(), "event": {"timestamp": 1.0}},
        ):
            with self.assertRaises(NodeMessageError):
                encode_node_message(dict(valid, payload=payload))

    def test_dedicated_constructors_enforce_typed_payloads(self):
        common = {
            "source": "rover",
            "target": "aircraft_2",
            "command_id": self.command_id,
            "sequence": 5,
            "timestamp": 20.0,
        }
        messages = [
            build_command_message(action="guided", parameters={}, **common),
            build_status_message(
                **self.status_payload(),
                source="aircraft_2",
                target="rover",
                command_id=self.command_id,
                sequence=6,
                timestamp=20.0,
            ),
            build_ack_message(stage="RECEIVED", duplicate=False, **common),
            build_nack_message(reason="queue_full", **common),
        ]
        self.assertEqual([m["type"] for m in messages], [
            "command", "status", "ack", "nack",
        ])

        invalid = [
            dict(messages[0], payload={}),
            dict(messages[0], payload={"action": [], "parameters": {}}),
            dict(messages[0], payload={"action": "guided", "parameters": []}),
            dict(messages[1], payload={"online": "yes"}),
            dict(messages[2], payload={"stage": "MAYBE"}),
            dict(messages[2], payload={"stage": []}),
            dict(messages[3], payload={}),
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(NodeMessageError):
                encode_node_message(candidate)

    def test_aircraft_2_network_phone_command_is_typed(self):
        message = build_command_message(
            action="network_phone",
            parameters={},
            source="rover",
            target="aircraft_2",
            command_id=self.command_id,
            sequence=8,
            timestamp=20.0,
        )

        decoded = decode_node_message(
            encode_node_message(message), expected_target="aircraft_2"
        )

        self.assertEqual(decoded["payload"]["action"], "network_phone")

    def test_mission_constructors_require_count_digest_and_ordered_items(self):
        items = [
            {"index": 0, "lat": 32.1, "lon": 118.9, "alt": 20.0},
            {"index": 1, "lat": 32.2, "lon": 119.0, "alt": 20.0},
        ]
        digest = mission_digest(items)
        common = {
            "source": "rover",
            "target": "aircraft_2",
            "command_id": self.command_id,
            "timestamp": 20.0,
        }
        begin = build_mission_begin_message(
            mission_id="mission-1", item_count=2, digest=digest,
            sequence=10, **common,
        )
        item = build_mission_item_message(
            mission_id="mission-1", index=0, item=items[0],
            sequence=11, **common,
        )
        commit = build_mission_commit_message(
            mission_id="mission-1", item_count=2, digest=digest,
            sequence=12, **common,
        )
        self.assertEqual(begin["payload"]["digest"], digest)
        self.assertEqual(item["payload"]["item"]["index"], 0)
        self.assertEqual(commit["payload"]["item_count"], 2)

        with self.assertRaises(NodeMessageError):
            build_mission_begin_message(
                mission_id="mission-1", item_count=2, digest="bad",
                sequence=10, **common,
            )
        with self.assertRaises(NodeMessageError):
            build_mission_item_message(
                mission_id="mission-1", index=1, item=items[0],
                sequence=11, **common,
            )
        self.assertEqual(
            build_mission_begin_message(
                mission_id="max", item_count=100, digest=digest,
                sequence=13, **common,
            )["payload"]["item_count"],
            100,
        )
        with self.assertRaises(NodeMessageError):
            build_mission_begin_message(
                mission_id="too-many", item_count=101, digest=digest,
                sequence=14, **common,
            )
        for bad_item in (
            {**items[0], "command": 65536},
            {**items[0], "frame": 256},
        ):
            with self.assertRaises(NodeMessageError):
                build_mission_item_message(
                    mission_id="mission-1", index=0, item=bad_item,
                    sequence=15, **common,
                )

    def test_rejects_wrong_target_invalid_type_and_oversized_datagram(self):
        message = make_node_message(
            "ack",
            source="aircraft_2",
            target="rover",
            command_id=self.command_id,
            sequence=2,
            timestamp=11.0,
            payload={"stage": "RECEIVED"},
        )

        with self.assertRaisesRegex(NodeMessageError, "target"):
            decode_node_message(encode_node_message(message), expected_target="aircraft_2")
        with self.assertRaises(NodeMessageError):
            make_node_message(
                "raw_mavlink",
                source="rover",
                target="aircraft_2",
                command_id=self.command_id,
                sequence=3,
                timestamp=12.0,
                payload={},
            )
        with self.assertRaisesRegex(NodeMessageError, "size"):
            decode_node_message(b"{" + b"x" * MAX_DATAGRAM_BYTES + b"}")
        oversized = dict(message)
        oversized["payload"] = {"stage": "RECEIVED", "detail": "x" * MAX_DATAGRAM_BYTES}
        with self.assertRaisesRegex(NodeMessageError, "size"):
            encode_node_message(oversized)

    def test_mission_digest_is_ordered_canonical_and_sha256(self):
        first = [
            {"index": 0, "lat": 32.1, "lon": 118.9, "alt": 20.0},
            {"index": 1, "lat": 32.2, "lon": 119.0, "alt": 20.0},
        ]
        same = [
            {"lon": 118.9, "alt": 20.0, "lat": 32.1, "index": 0},
            {"alt": 20.0, "index": 1, "lon": 119.0, "lat": 32.2},
        ]
        reversed_items = list(reversed(first))

        self.assertEqual(mission_digest(first), mission_digest(same))
        self.assertNotEqual(mission_digest(first), mission_digest(reversed_items))
        self.assertRegex(mission_digest(first), r"^[0-9a-f]{64}$")
        self.assertEqual(
            mission_digest(first),
            "4f6ea524e77e14ad8a04749f3680313ac574a69342ea5349c176f9c2b7b8c9b7",
        )

    def test_malformed_nested_json_and_impossible_status_are_protocol_errors(self):
        nested = ("[" * 1100 + "0" + "]" * 1100).encode("ascii")
        self.assertLess(len(nested), MAX_DATAGRAM_BYTES)
        with self.assertRaises(NodeMessageError):
            decode_node_message(nested)

        nested_value = 0
        for _ in range(1100):
            nested_value = [nested_value]
        deeply_nested_message = {
            "version": 1,
            "type": "command",
            "source": "rover",
            "target": "aircraft_2",
            "command_id": self.command_id,
            "sequence": 19,
            "timestamp": 20.0,
            "payload": {"action": "guided", "parameters": {"nested": nested_value}},
        }
        with self.assertRaises(NodeMessageError):
            encode_node_message(deeply_nested_message)

        valid = make_node_message(
            "status",
            source="aircraft_2",
            target="rover",
            command_id=self.command_id,
            sequence=20,
            timestamp=20.0,
            payload=self.status_payload(),
        )
        for field, value in (("lat", 90.01), ("lon", -180.01)):
            candidate = json.loads(json.dumps(valid))
            candidate["payload"][field] = value
            with self.subTest(field=field), self.assertRaises(NodeMessageError):
                encode_node_message(candidate)


if __name__ == "__main__":
    unittest.main()
