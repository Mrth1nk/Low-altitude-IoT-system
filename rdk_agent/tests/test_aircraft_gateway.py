from aircraft_gateway import AircraftGatewayState, parse_payload


def test_parse_heartbeat_reports_mode_and_arm_state():
    payload = bytes([4, 0, 0, 0, 0, 0, 0x80, 0, 0])

    parsed = parse_payload(0, 1, 1, payload)

    assert parsed is not None
    assert parsed["type"] == "HEARTBEAT"
    assert "GUIDED" in parsed["text"]
    assert "armed=YES" in parsed["text"]


def test_gateway_snapshot_marks_link_active_after_packet():
    state = AircraftGatewayState()

    state.record_packet(14560, b"hello", ("192.168.144.10", 14560))
    snapshot = state.snapshot()

    assert snapshot["ok"] is True
    assert snapshot["link_active"] is True
    assert snapshot["packets_received"] == 1
    assert snapshot["bytes_received"] == 5
    assert snapshot["last_remote"] == "192.168.144.10:14560 -> UDP 14560"
    assert snapshot["messages"][-1]["type"] == "RAW"


def test_parse_mission_request_and_ack_for_upload_handshake():
    request = parse_payload(51, 1, 1, bytes([1, 0, 255, 190, 0]))
    ack = parse_payload(47, 1, 1, bytes([255, 190, 0, 0]))

    assert request is not None
    assert request["type"] == "MISSION_REQUEST_INT"
    assert request["seq"] == 1
    assert "seq=1" in request["text"]
    assert ack is not None
    assert ack["type"] == "MISSION_ACK"
    assert ack["result"] == 0
