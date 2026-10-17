from __future__ import annotations

import json

import pytest

from game_engine import serialize_nlh_hand_state
from logic.first_party_table import FirstPartyNLHGame
from logic.table_checkpoint import (
    TABLE_CHECKPOINT_VERSION,
    deserialize_first_party_table,
    serialize_first_party_table,
)


def make_game() -> FirstPartyNLHGame:
    game = FirstPartyNLHGame(
        "checkpoint-table",
        10,
        20,
        100,
        1000,
        max_seats=6,
        union_id="checkpoint-union",
        origin_club_id="checkpoint-club",
        table_name="Checkpoint Table",
    )
    assert game.add_player(1, "alice", "Alice", 500, "buy-in-a", "club-a")
    assert game.add_player(4, "bob", "Bob", 500, "buy-in-b", "club-b")
    game.update_table_settings(
        jackpot_enabled=True,
        jackpot_fee=5,
        jackpot_min_pot=100,
        jackpot_qualifying_hand="four-of-a-kind",
        rake_rate=0.05,
        rake_cap=25,
        rake_min_pot=50,
        hand_of_day_enabled=True,
        hand_of_day_target=500,
        hand_of_day_prize=50,
    )
    return game


def test_active_table_checkpoint_round_trip_preserves_runtime_and_continuation() -> None:
    game = make_game()
    assert game.start_new_hand()
    actor = game.current_actor_user_id
    assert actor is not None
    other = "bob" if actor == "alice" else "alice"
    assert game.set_pre_action(other, "CALL_ANY") == "CALL_ANY"

    checkpoint = serialize_first_party_table(game)
    restored = deserialize_first_party_table(json.loads(json.dumps(checkpoint)))

    assert checkpoint["schema_version"] == TABLE_CHECKPOINT_VERSION
    assert serialize_first_party_table(restored) == checkpoint
    assert restored._pending_pre_actions == {other: "CALL_ANY"}
    assert restored._player_order == game._player_order
    assert restored._button_offset == game._button_offset
    assert restored.table_settings.as_dict() == game.table_settings.as_dict()
    assert restored._replay_frames == game._replay_frames
    for user_id in ("alice", "bob"):
        assert restored.get_masked_state_for_player(user_id) == game.get_masked_state_for_player(user_id)

    action = "CALL" if (game.current_call_amount or 0) > 0 else "CHECK"
    assert game.handle_player_action(actor, action)
    assert restored.handle_player_action(actor, action)
    assert serialize_nlh_hand_state(restored.poker_game) == serialize_nlh_hand_state(game.poker_game)
    assert {index: seat.stack for index, seat in restored.seats.items()} == {
        index: seat.stack for index, seat in game.seats.items()
    }
    assert restored._engine_events == game._engine_events
    restored_action = restored._replay_frames[-1]["last_action"]
    original_action = game._replay_frames[-1]["last_action"]
    assert {key: value for key, value in restored_action.items() if key != "timestamp"} == {
        key: value for key, value in original_action.items() if key != "timestamp"
    }


def test_table_checkpoint_preserves_unacknowledged_completed_hand() -> None:
    game = make_game()
    assert game.start_new_hand()
    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    assert game.peek_completed_hand() is not None

    restored = deserialize_first_party_table(serialize_first_party_table(game))

    assert not restored.is_hand_running
    assert restored.peek_completed_hand() == game.peek_completed_hand()
    assert serialize_first_party_table(restored) == serialize_first_party_table(game)


def test_table_checkpoint_rejects_inconsistent_seat_and_hand_state() -> None:
    game = make_game()
    assert game.start_new_hand()
    checkpoint = serialize_first_party_table(game)
    checkpoint["seats"][0]["user_id"] = "wrong-player"

    with pytest.raises(ValueError, match="must match their table seats"):
        deserialize_first_party_table(checkpoint)


def test_table_checkpoint_rejects_unknown_version() -> None:
    checkpoint = serialize_first_party_table(make_game())
    checkpoint["schema_version"] = TABLE_CHECKPOINT_VERSION + 1

    with pytest.raises(ValueError, match="Unsupported table checkpoint version"):
        deserialize_first_party_table(checkpoint)