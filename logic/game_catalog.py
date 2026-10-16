from __future__ import annotations

from typing import Any


SUPPORTED_GAME_ID = "no_limit_texas_holdem"
SUPPORTED_GAME_IDS = frozenset({SUPPORTED_GAME_ID})

_GAME_REGISTRY = (
    {
        "game_id": SUPPORTED_GAME_ID,
        "display_name": "No Limit Texas Hold'em",
        "category": "Hold'em",
        "backend_status": "supported",
        "create_supported": True,
    },
)


def is_game_create_supported(game_id: str) -> bool:
    return game_id in SUPPORTED_GAME_IDS


def get_game_catalog() -> list[dict[str, Any]]:
    return [dict(game) for game in _GAME_REGISTRY]