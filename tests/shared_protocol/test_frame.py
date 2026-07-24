import json
import struct
import unittest
import uuid
import zlib

from shared_protocol.frame import (
    DEFAULT_MAX_PAYLOAD_LENGTH,
    MAGIC,
    VERSION,
    Frame,
    FrameDecoder,
    FrameError,
    MessageType,
    decode_frame,
    encode_frame,
)
from shared_protocol.messages import validate_payload


class FrameTests(unittest.TestCase):
    def setUp(self):
        self.command_id = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")

    def test_round_trip_uses_canonical_json_and_crc32(self):
        frame = Frame(
            MessageType.COMMAND,
            flags=3,
            sequence=42,
            command_id=self.command_id,
            payload={"mode": "GUIDED", "action": "set_mode"},
        )

        encoded = encode_frame(frame)

        self.assertEqual(encoded[:4], MAGIC)
        self.assertEqual(encoded[4], VERSION)
        payload_length = struct.unpack_from(">I", encoded, 27)[0]
        payload = encoded[31 : 31 + payload_length]
        self.assertEqual(payload, b'{"action":"set_mode","mode":"GUIDED"}')
        self.assertEqual(
            struct.unpack_from(">I", encoded, len(encoded) - 4)[0],
            zlib.crc32(encoded[:-4]) & 0xFFFFFFFF,
        )
        self.assertEqual(decode_frame(encoded), frame)

    def test_rejects_bad_magic_version_length_and_crc(self):
        encoded = bytearray(
            encode_frame(
                Frame(
                    MessageType.STATUS,
                    0,
                    1,
                    self.command_id,
                    {"state": "ready"},
                )
            )
        )

        for index, replacement in ((0, ord("X")), (4, 2), (-1, encoded[-1] ^ 0xFF)):
            damaged = bytearray(encoded)
            damaged[index] = replacement
            with self.subTest(index=index), self.assertRaises(FrameError):
                decode_frame(bytes(damaged))

        with self.assertRaises(FrameError):
            decode_frame(bytes(encoded[:-1]))

    def test_stream_decoder_handles_split_and_concatenated_frames(self):
        first = Frame(
            MessageType.ACK, 0, 7, self.command_id, {"acked_sequence": 6}
        )
        second = Frame(
            MessageType.STATUS, 0, 8, self.command_id, {"state": "uploaded"}
        )
        wire = encode_frame(first) + encode_frame(second)
        decoder = FrameDecoder()

        self.assertEqual(decoder.feed(wire[:9]), [])
        self.assertEqual(decoder.feed(wire[9:37]), [])
        self.assertEqual(decoder.feed(wire[37:]), [first, second])

    def test_stream_decoder_resynchronizes_after_noise_bad_crc_and_bad_length(self):
        valid = Frame(
            MessageType.LINK_BLOCKED,
            0,
            12,
            self.command_id,
            {"reason": "optical_lost"},
        )
        bad_crc = bytearray(encode_frame(valid))
        bad_crc[-1] ^= 0xFF
        bad_length = bytearray(encode_frame(valid))
        struct.pack_into(">I", bad_length, 27, 0xFFFFFFFF)
        decoder = FrameDecoder(max_payload_length=4096)

        decoded = decoder.feed(
            b"noise" + bytes(bad_crc) + bytes(bad_length) + encode_frame(valid)
        )

        self.assertEqual(decoded, [valid])

    def test_stream_decoder_skips_incomplete_false_frame_before_complete_frame(self):
        valid = Frame(
            MessageType.STATUS,
            0,
            13,
            self.command_id,
            {"state": "ready"},
        )
        false_header = bytearray(encode_frame(valid)[:31])
        struct.pack_into(">I", false_header, 27, 1000)
        decoder = FrameDecoder(max_payload_length=4096)

        decoded = decoder.feed(bytes(false_header) + b"partial" + encode_frame(valid))

        self.assertEqual(decoded, [valid])

    def test_encoder_enforces_default_and_configurable_payload_limit(self):
        oversized = Frame(
            MessageType.STATUS,
            0,
            14,
            self.command_id,
            {"state": "ready", "detail": "x" * DEFAULT_MAX_PAYLOAD_LENGTH},
        )
        small = Frame(
            MessageType.STATUS,
            0,
            15,
            self.command_id,
            {"state": "ready", "detail": "12345"},
        )

        with self.assertRaises(FrameError):
            encode_frame(oversized)
        with self.assertRaises(FrameError):
            encode_frame(small, max_payload_length=10)
        self.assertEqual(
            decode_frame(encode_frame(small, max_payload_length=128)),
            small,
        )


class TypedPayloadTests(unittest.TestCase):
    def test_accepts_checksum_and_bounded_mavlink_mission_fields(self):
        checksum = "a" * 64
        valid = {
            MessageType.MISSION_BEGIN: {
                "mission_id": "demo",
                "item_count": 1,
                "vehicle": "aircraft",
                "checksum": checksum,
            },
            MessageType.MISSION_ITEM: {
                "mission_id": "demo",
                "index": 0,
                "lat": 32.1,
                "lon": 118.9,
                "alt": 20.0,
                "command": 16,
                "frame": 6,
                "param1": 0.0,
                "param2": 2.0,
                "param3": 0.0,
                "param4": 0.0,
                "autocontinue": True,
            },
            MessageType.MISSION_COMMIT: {
                "mission_id": "demo",
                "item_count": 1,
                "checksum": checksum,
            },
        }

        for message_type, payload in valid.items():
            with self.subTest(message_type=message_type):
                self.assertEqual(validate_payload(message_type, payload), payload)

    def test_rejects_invalid_checksum_and_mavlink_field_bounds(self):
        invalid = [
            (
                MessageType.MISSION_BEGIN,
                {
                    "mission_id": "m",
                    "item_count": 1,
                    "vehicle": "aircraft",
                    "checksum": "not-sha256",
                },
            ),
            (
                MessageType.MISSION_ITEM,
                {
                    "mission_id": "m",
                    "index": 0,
                    "lat": 0.0,
                    "lon": 0.0,
                    "alt": 1.0,
                    "command": 70000,
                    "frame": 6,
                    "param1": 0.0,
                    "param2": 0.0,
                    "param3": 0.0,
                    "param4": 0.0,
                    "autocontinue": True,
                },
            ),
        ]

        for message_type, payload in invalid:
            with self.subTest(message_type=message_type), self.assertRaises(
                (TypeError, ValueError)
            ):
                validate_payload(message_type, payload)

    def test_accepts_all_required_message_payloads(self):
        valid = {
            MessageType.COMMAND: {"action": "arm", "parameters": {}},
            MessageType.MISSION_BEGIN: {
                "mission_id": "mission-1",
                "item_count": 2,
                "vehicle": "aircraft",
            },
            MessageType.MISSION_ITEM: {
                "mission_id": "mission-1",
                "index": 0,
                "lat": 32.1,
                "lon": 118.9,
                "alt": 20.0,
            },
            MessageType.MISSION_COMMIT: {
                "mission_id": "mission-1",
                "item_count": 2,
            },
            MessageType.ACK: {"acked_sequence": 9},
            MessageType.NACK: {"acked_sequence": 9, "reason": "bad_item"},
            MessageType.STATUS: {"state": "verified", "detail": "2 items"},
            MessageType.LINK_BLOCKED: {"reason": "optical_lost"},
        }

        for message_type, payload in valid.items():
            with self.subTest(message_type=message_type):
                self.assertEqual(validate_payload(message_type, payload), payload)

    def test_rejects_missing_wrong_typed_and_unknown_fields(self):
        invalid = [
            (MessageType.COMMAND, {"parameters": {}}),
            (
                MessageType.MISSION_BEGIN,
                {"mission_id": "m", "item_count": -1, "vehicle": "aircraft"},
            ),
            (
                MessageType.MISSION_ITEM,
                {
                    "mission_id": "m",
                    "index": 0,
                    "lat": 91.0,
                    "lon": 0.0,
                    "alt": 1.0,
                },
            ),
            (MessageType.MISSION_COMMIT, {"mission_id": "m", "item_count": "2"}),
            (MessageType.ACK, {"acked_sequence": -1}),
            (MessageType.NACK, {"acked_sequence": 1, "reason": ""}),
            (MessageType.STATUS, {"state": "ok", "unexpected": True}),
            (MessageType.LINK_BLOCKED, {}),
        ]

        for message_type, payload in invalid:
            with self.subTest(message_type=message_type), self.assertRaises(
                (TypeError, ValueError)
            ):
                validate_payload(message_type, payload)

    def test_rejects_missions_larger_than_operational_limit(self):
        with self.assertRaises(ValueError):
            validate_payload(
                MessageType.MISSION_BEGIN,
                {
                    "mission_id": "too-large",
                    "item_count": 101,
                    "vehicle": "aircraft",
                },
            )
        with self.assertRaises(ValueError):
            validate_payload(
                MessageType.MISSION_COMMIT,
                {"mission_id": "too-large", "item_count": 101},
            )
        with self.assertRaises(ValueError):
            validate_payload(
                MessageType.MISSION_ITEM,
                {
                    "mission_id": "too-large",
                    "index": 100,
                    "lat": 0.0,
                    "lon": 0.0,
                    "alt": 1.0,
                },
            )

    def test_frame_encoding_validates_payload_and_json_serializability(self):
        with self.assertRaises(ValueError):
            encode_frame(
                Frame(
                    MessageType.COMMAND,
                    0,
                    1,
                    uuid.uuid4(),
                    {"action": "arm", "parameters": {}, "extra": object()},
                )
            )

        canonical = json.dumps(
            {"b": 1, "a": 2}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(canonical, b'{"a":2,"b":1}')


if __name__ == "__main__":
    unittest.main()
