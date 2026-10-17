from __future__ import annotations

import json
from typing import Any


class PubSubMessageError(ValueError):
    pass


def encode_worker_command(
    *,
    gateway_id: str,
    request_id: str,
    worker_id: str,
    scope_type: str,
    scope_id: str,
    table_id: str,
    command: dict[str, Any],
) -> bytes:
    if not all((gateway_id, request_id, worker_id, scope_id, table_id)):
        raise ValueError("Gateway, request, worker, scope, and table IDs are required.")
    if scope_type not in {"club", "union"}:
        raise ValueError("Scope type must be club or union.")
    if not isinstance(command, dict) or not command:
        raise ValueError("Worker command must be a non-empty object.")
    try:
        return json.dumps(
            {
                "schema_version": 1,
                "gateway_id": gateway_id,
                "request_id": request_id,
                "worker_id": worker_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "table_id": table_id,
                "command": command,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("Worker command must contain JSON-compatible values.") from error


def decode_worker_command(data: bytes, *, expected_worker_id: str) -> dict[str, Any]:
    try:
        message = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PubSubMessageError("Worker command is not valid JSON.") from error
    if not isinstance(message, dict) or message.get("schema_version") != 1:
        raise PubSubMessageError("Worker command schema version is unsupported.")
    if message.get("worker_id") != expected_worker_id:
        raise PubSubMessageError("Worker command was delivered to the wrong worker.")
    if (
        not isinstance(message.get("gateway_id"), str)
        or not message["gateway_id"]
        or not isinstance(message.get("request_id"), str)
        or not message["request_id"]
        or message.get("scope_type") not in {"club", "union"}
        or not isinstance(message.get("scope_id"), str)
        or not message["scope_id"]
        or not isinstance(message.get("table_id"), str)
        or not message["table_id"]
        or not isinstance(message.get("command"), dict)
        or not message["command"]
    ):
        raise PubSubMessageError("Worker command is missing required routing data.")
    return message


def encode_worker_reply(
    *,
    gateway_id: str,
    request_id: str,
    worker_id: str,
    result: dict[str, Any],
) -> bytes:
    if not gateway_id or not request_id or not worker_id:
        raise ValueError("Gateway, request, and worker IDs are required.")
    if not isinstance(result, dict):
        raise ValueError("Worker result must be an object.")
    return json.dumps(
        {
            "schema_version": 1,
            "gateway_id": gateway_id,
            "request_id": request_id,
            "worker_id": worker_id,
            "result": result,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def decode_worker_reply(data: bytes, *, expected_gateway_id: str) -> dict[str, Any]:
    try:
        message = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PubSubMessageError("Worker reply is not valid JSON.") from error
    if (
        not isinstance(message, dict)
        or message.get("schema_version") != 1
        or message.get("gateway_id") != expected_gateway_id
        or not isinstance(message.get("request_id"), str)
        or not message["request_id"]
        or not isinstance(message.get("worker_id"), str)
        or not message["worker_id"]
        or not isinstance(message.get("result"), dict)
    ):
        raise PubSubMessageError("Worker reply routing data is invalid.")
    return message
