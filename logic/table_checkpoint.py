from __future__ import annotations

import json
from typing import Any

from game_engine import NLHHandState, PokerEvent, PokerEventKind, Card
from game_engine.checkpoint import deserialize_nlh_hand_state, serialize_nlh_hand_state
from logic.first_party_table import FirstPartyNLHGame
from logic.table_game_base import PlayerSeat, TableSettings


TABLE_CHECKPOINT_VERSION = 1
PRE_ACTIONS = {"CHECK_FOLD", "FOLD", "CALL_ANY"}


def serialize_first_party_table(game: FirstPartyNLHGame) -> dict[str, Any]:
    if game.poker_game is not None and not isinstance(game.poker_game, NLHHandState):
        raise ValueError("Only first-party NLH game states can be checkpointed.")
    return {
        "schema_version": TABLE_CHECKPOINT_VERSION,
        "table": {
            "table_id": game.table_id,
            "game_id": game.game_id,
            "small_blind": game.small_blind,
            "big_blind": game.big_blind,
            "buy_in_min": game.buy_in_min,
            "buy_in_max": game.buy_in_max,
            "max_seats": game.max_seats,
            "union_id": game.union_id,
            "origin_club_id": game.origin_club_id,
            "table_name": game.table_name,
            "theme_id": game.theme_id,
            "union_code": game.union_code,
        },
        "settings": game.table_settings.as_dict(),
        "pools": {
            "jackpot_pool": game.jackpot_pool,
            "rake_pool": game.rake_pool,
            "hand_of_day_pool": game.hand_of_day_pool,
        },
        "seats": [
            {
                "seat_index": seat_index,
                "user_id": seat.user_id,
                "username": seat.username,
                "stack": seat.stack,
                "buy_in_id": seat.buy_in_id,
                "buy_in_amount": seat.buy_in_amount,
                "status": seat.status,
                "club_id": seat.club_id,
                "new_to_table": seat.new_to_table,
                "accept_big_blind_post": seat.accept_big_blind_post,
            }
            for seat_index, seat in sorted(game.seats.items())
        ],
        "hand_state": (
            serialize_nlh_hand_state(game.poker_game)
            if isinstance(game.poker_game, NLHHandState)
            else None
        ),
        "runtime_state": {
            "is_hand_running": game.is_hand_running,
            "player_order": list(game._player_order),
            "button_offset": game._button_offset,
            "current_hand_id": game._current_hand_id,
            "hand_started_at": game._hand_started_at,
            "hand_hole_cards": [list(cards) for cards in game._hand_hole_cards],
            "replay_frames": _json_copy(game._replay_frames, "replay_frames"),
            "pending_pre_actions": dict(game._pending_pre_actions),
            "has_started_hand": game._has_started_hand,
            "completed_hand": _json_copy(game._completed_hand, "completed_hand"),
            "engine_events": [_serialize_event(event) for event in getattr(game, "_engine_events", [])],
        },
    }


def deserialize_first_party_table(checkpoint: object) -> FirstPartyNLHGame:
    root = _object(checkpoint, "checkpoint")
    version = _integer(root.get("schema_version"), "schema_version", minimum=1)
    if version != TABLE_CHECKPOINT_VERSION:
        raise ValueError("Unsupported table checkpoint version.")

    table = _object(root.get("table"), "table")
    table_id = _string(table.get("table_id"), "table.table_id")
    game_id = _string(table.get("game_id"), "table.game_id")
    game = FirstPartyNLHGame(
        table_id,
        _integer(table.get("small_blind"), "table.small_blind", minimum=1),
        _integer(table.get("big_blind"), "table.big_blind", minimum=1),
        _integer(table.get("buy_in_min"), "table.buy_in_min", minimum=1),
        _integer(table.get("buy_in_max"), "table.buy_in_max", minimum=1),
        max_seats=_integer(table.get("max_seats"), "table.max_seats", minimum=2),
        union_id=_optional_string(table.get("union_id"), "table.union_id"),
        origin_club_id=_optional_string(table.get("origin_club_id"), "table.origin_club_id"),
        table_name=_string(table.get("table_name"), "table.table_name"),
        theme_id=_optional_string(table.get("theme_id"), "table.theme_id") or "standard",
        union_code=_optional_string(table.get("union_code"), "table.union_code"),
        game_id=game_id,
    )
    game.table_settings = _deserialize_settings(root.get("settings"))
    pools = _object(root.get("pools"), "pools")
    game.jackpot_pool = _integer(pools.get("jackpot_pool"), "pools.jackpot_pool", minimum=0)
    game.rake_pool = _integer(pools.get("rake_pool"), "pools.rake_pool", minimum=0)
    game.hand_of_day_pool = _integer(pools.get("hand_of_day_pool"), "pools.hand_of_day_pool", minimum=0)
    game.seats = _deserialize_seats(root.get("seats"), game.max_seats)

    hand_state_data = root.get("hand_state")
    game.poker_game = (
        deserialize_nlh_hand_state(hand_state_data)
        if hand_state_data is not None
        else None
    )
    runtime = _object(root.get("runtime_state"), "runtime_state")
    game.is_hand_running = _boolean(runtime.get("is_hand_running"), "runtime_state.is_hand_running")
    game._player_order = _integer_list(runtime.get("player_order"), "runtime_state.player_order")
    game._button_offset = _integer(runtime.get("button_offset"), "runtime_state.button_offset", minimum=0)
    game._current_hand_id = _optional_string(runtime.get("current_hand_id"), "runtime_state.current_hand_id")
    game._hand_started_at = _optional_string(runtime.get("hand_started_at"), "runtime_state.hand_started_at")
    game._hand_hole_cards = _string_matrix(runtime.get("hand_hole_cards"), "runtime_state.hand_hole_cards")
    game._replay_frames = _json_copy(runtime.get("replay_frames"), "replay_frames")
    game._pending_pre_actions = _deserialize_pre_actions(runtime.get("pending_pre_actions"))
    game._has_started_hand = _boolean(runtime.get("has_started_hand"), "runtime_state.has_started_hand")
    completed_hand = runtime.get("completed_hand")
    game._completed_hand = _json_copy(completed_hand, "completed_hand") if completed_hand is not None else None
    game._engine_events = _deserialize_events(runtime.get("engine_events"))

    _validate_table_consistency(game)
    return game


def _deserialize_settings(value: object) -> TableSettings:
    settings = _object(value, "settings")
    expected = TableSettings().as_dict()
    if set(settings) != set(expected):
        raise ValueError("Checkpoint table settings do not match the supported schema.")
    for field in ("jackpot_enabled", "no_flop_no_drop", "hand_of_day_enabled"):
        _boolean(settings[field], f"settings.{field}")
    for field in ("jackpot_fee", "jackpot_min_pot", "rake_cap", "rake_min_pot", "hand_of_day_target", "hand_of_day_prize"):
        _integer(settings[field], f"settings.{field}", minimum=0)
    rate = settings["rake_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 0.1:
        raise ValueError("settings.rake_rate must be between 0 and 0.1.")
    qualifying_hand = _optional_string(settings["jackpot_qualifying_hand"], "settings.jackpot_qualifying_hand")
    return TableSettings(
        jackpot_enabled=settings["jackpot_enabled"],
        jackpot_fee=settings["jackpot_fee"],
        jackpot_min_pot=settings["jackpot_min_pot"],
        jackpot_qualifying_hand=qualifying_hand,
        rake_rate=float(rate),
        rake_cap=settings["rake_cap"],
        rake_min_pot=settings["rake_min_pot"],
        no_flop_no_drop=settings["no_flop_no_drop"],
        hand_of_day_enabled=settings["hand_of_day_enabled"],
        hand_of_day_target=settings["hand_of_day_target"],
        hand_of_day_prize=settings["hand_of_day_prize"],
    )


def _deserialize_seats(value: object, max_seats: int) -> dict[int, PlayerSeat]:
    entries = _list(value, "seats")
    seats: dict[int, PlayerSeat] = {}
    user_ids: set[str] = set()
    for index, value in enumerate(entries):
        label = f"seats[{index}]"
        data = _object(value, label)
        seat_index = _integer(data.get("seat_index"), f"{label}.seat_index", minimum=0)
        if seat_index >= max_seats or seat_index in seats:
            raise ValueError(f"{label}.seat_index must be unique and within table capacity.")
        user_id = _string(data.get("user_id"), f"{label}.user_id")
        if user_id in user_ids:
            raise ValueError("Checkpoint cannot seat the same user more than once.")
        user_ids.add(user_id)
        buy_in_id = _optional_string(data.get("buy_in_id"), f"{label}.buy_in_id")
        seats[seat_index] = PlayerSeat(
            user_id=user_id,
            username=_string(data.get("username"), f"{label}.username"),
            stack=_integer(data.get("stack"), f"{label}.stack", minimum=0),
            buy_in_id=buy_in_id,
            buy_in_amount=_integer(data.get("buy_in_amount"), f"{label}.buy_in_amount", minimum=0),
            status=_string(data.get("status"), f"{label}.status"),
            club_id=_optional_string(data.get("club_id"), f"{label}.club_id"),
            new_to_table=_boolean(data.get("new_to_table"), f"{label}.new_to_table"),
            accept_big_blind_post=_boolean(data.get("accept_big_blind_post"), f"{label}.accept_big_blind_post"),
        )
    return seats


def _deserialize_pre_actions(value: object) -> dict[str, str]:
    actions = _object(value, "runtime_state.pending_pre_actions")
    result: dict[str, str] = {}
    for user_id, option in actions.items():
        key = _string(user_id, "runtime_state.pending_pre_actions key")
        action = _string(option, f"runtime_state.pending_pre_actions.{key}")
        if action not in PRE_ACTIONS:
            raise ValueError(f"Unsupported pending pre-action: {action}.")
        result[key] = action
    return result


def _serialize_event(event: PokerEvent) -> dict[str, Any]:
    return {
        "kind": event.kind.value,
        "player_id": event.player_id,
        "amount": event.amount,
        "cards": [card.code for card in event.cards],
    }


def _deserialize_events(value: object) -> list[PokerEvent]:
    events = _list(value, "runtime_state.engine_events")
    result = []
    for index, value in enumerate(events):
        label = f"runtime_state.engine_events[{index}]"
        event = _object(value, label)
        try:
            kind = PokerEventKind(_string(event.get("kind"), f"{label}.kind"))
        except ValueError as error:
            raise ValueError(f"{label}.kind is not supported.") from error
        player_id = _optional_string(event.get("player_id"), f"{label}.player_id")
        amount = event.get("amount")
        if amount is not None:
            amount = _integer(amount, f"{label}.amount", minimum=0)
        cards = _cards(event.get("cards"), f"{label}.cards")
        result.append(PokerEvent(kind=kind, player_id=player_id, amount=amount, cards=cards))
    return result


def _validate_table_consistency(game: FirstPartyNLHGame) -> None:
    hand_state = game.poker_game
    if hand_state is None:
        if game.is_hand_running or game._current_hand_id is not None:
            raise ValueError("Runtime hand markers require a saved NLH hand state.")
        return

    if hand_state.table_id != game.table_id or hand_state.config.game_id != game.game_id:
        raise ValueError("Saved NLH hand state does not belong to this table.")
    if game._current_hand_id != hand_state.hand_id:
        raise ValueError("Current hand ID does not match the saved NLH hand state.")
    if game.is_hand_running != (hand_state.street.value != 5):
        raise ValueError("Running-hand flag does not match the saved NLH street.")
    for player in hand_state.players:
        seat = game.seats.get(player.seat_index)
        if seat is None or seat.user_id != player.player_id:
            raise ValueError("Saved NLH players must match their table seats.")
    if any(seat not in game.seats for seat in game._player_order):
        raise ValueError("Player order contains a seat that is not occupied.")
    if any(user_id not in {seat.user_id for seat in game.seats.values()} for user_id in game._pending_pre_actions):
        raise ValueError("Pending pre-actions must belong to seated players.")
    if game._completed_hand is not None and game._completed_hand.get("hand_id") != hand_state.hand_id:
        raise ValueError("Pending completed hand does not match the current hand ID.")


def _json_copy(value: Any, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain JSON-compatible data.") from error


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"{label} must be null or a non-empty string.")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer greater than or equal to {minimum}.")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean.")
    return value


def _integer_list(value: object, label: str) -> list[int]:
    entries = _list(value, label)
    return [
        _integer(entry, f"{label}[{index}]", minimum=0)
        for index, entry in enumerate(entries)
    ]


def _string_matrix(value: object, label: str) -> list[list[str]]:
    rows = _list(value, label)
    matrix: list[list[str]] = []
    for row_index, row in enumerate(rows):
        entries = _list(row, f"{label}[{row_index}]")
        matrix.append(
            [
                _string(entry, f"{label}[{row_index}][{column_index}]")
                for column_index, entry in enumerate(entries)
            ]
        )
    return matrix


def _cards(value: object, label: str) -> tuple[Card, ...]:
    codes = _list(value, label)
    if any(not isinstance(code, str) for code in codes):
        raise ValueError(f"{label} must contain card-code strings.")
    return tuple(Card.from_code(code) for code in codes)