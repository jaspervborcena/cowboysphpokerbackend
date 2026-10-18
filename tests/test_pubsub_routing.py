from __future__ import annotations

import pytest

from logic.pubsub_routing import (
    PubSubMessageError,
    decode_worker_command,
    decode_worker_reply,
    encode_worker_command,
    encode_worker_reply,
)


def test_worker_command_round_trip_and_owner_routing_validation() -> None:
    encoded = encode_worker_command(
        gateway_id="gateway-1",
        request_id="request-1",
        worker_id="worker-a",
        scope_type="club",
        scope_id="club-1",
        table_id="table-1",
        command={"event": "PLAYER_ACTION", "user_id": "player-1", "action_id": "action-1"},
    )

    decoded = decode_worker_command(encoded, expected_worker_id="worker-a")

    assert decoded["scope_type"] == "club"
    assert decoded["scope_id"] == "club-1"
    assert decoded["table_id"] == "table-1"
    assert decoded["command"]["action_id"] == "action-1"
    with pytest.raises(PubSubMessageError, match="wrong worker"):
        decode_worker_command(encoded, expected_worker_id="worker-b")


def test_worker_reply_round_trip_and_gateway_isolation() -> None:
    encoded = encode_worker_reply(
        gateway_id="gateway-1",
        request_id="request-1",
        worker_id="worker-a",
        result={"accepted": True, "sequence": 4},
    )

    decoded = decode_worker_reply(encoded, expected_gateway_id="gateway-1")

    assert decoded["result"] == {"accepted": True, "sequence": 4}
    with pytest.raises(PubSubMessageError, match="routing data is invalid"):
        decode_worker_reply(encoded, expected_gateway_id="gateway-2")


def test_worker_command_rejects_non_json_values() -> None:
    with pytest.raises(ValueError, match="JSON-compatible"):
        encode_worker_command(
            gateway_id="gateway-1",
            request_id="request-1",
            worker_id="worker-a",
            scope_type="union",
            scope_id="union-1",
            table_id="table-1",
            command={"amount": object()},
        )
