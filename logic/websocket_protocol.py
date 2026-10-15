from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


PROTOCOL_VERSION = "1.0"


class UnsupportedWebSocketVersion(ValueError):
    pass


class MalformedWebSocketMessage(ValueError):
    pass


def game_topic(connection_scope: str, table_id: str) -> str:
    if connection_scope.startswith("union:"):
        union_id = connection_scope.removeprefix("union:")
        return f"unions/{union_id}/tables/{table_id}/game"
    return f"clubs/{connection_scope}/tables/{table_id}/game"


def make_envelope(
    event: str,
    payload: dict[str, Any],
    topic: str,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "topic": topic,
        "event": event,
        "correlation_id": correlation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def parse_client_envelope(
    message: Any,
    expected_topic: str,
) -> tuple[str, dict[str, Any], str]:
    if not isinstance(message, dict):
        raise MalformedWebSocketMessage("WebSocket envelope must be an object.")
    if message.get("version") != PROTOCOL_VERSION:
        raise UnsupportedWebSocketVersion("Unsupported WebSocket protocol version.")
    event = message.get("event")
    topic = message.get("topic")
    correlation_id = message.get("correlation_id")
    payload = message.get("payload")
    if not isinstance(event, str) or not event:
        raise MalformedWebSocketMessage("WebSocket envelope event is required.")
    if topic != expected_topic:
        raise MalformedWebSocketMessage("WebSocket envelope topic does not match this connection.")
    if not isinstance(correlation_id, str) or not correlation_id or len(correlation_id) > 128:
        raise MalformedWebSocketMessage("WebSocket envelope correlation_id must be a non-empty string of at most 128 characters.")
    if not isinstance(payload, dict):
        raise MalformedWebSocketMessage("WebSocket envelope payload must be an object.")
    return event, payload, correlation_id
