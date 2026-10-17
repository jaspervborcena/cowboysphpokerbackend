from __future__ import annotations

import json

import pytest

from game_engine import (
    Deck,
    NLH_CHECKPOINT_VERSION,
    PlayerSeed,
    PokerAction,
    PokerActionKind,
    PokerTableConfig,
    apply_nlh_action,
    deserialize_nlh_hand_state,
    project_player_hand_state,
    serialize_nlh_hand_state,
    start_nlh_hand,
)


def make_started_hand():
    return start_nlh_hand(
        "checkpoint-table",
        "checkpoint-hand",
        PokerTableConfig("no_limit_texas_holdem", 5, 10, 6),
        (PlayerSeed("alice", 1, 500, "Alice"), PlayerSeed("bob", 4, 500, "Bob")),
        button_seat=1,
        deck=Deck.for_testing(812),
    ).state


def test_in_progress_hand_checkpoint_round_trip_preserves_private_state_and_continuation() -> None:
    original = make_started_hand()
    checkpoint = serialize_nlh_hand_state(original)
    encoded = json.dumps(checkpoint)
    restored = deserialize_nlh_hand_state(json.loads(encoded))

    assert checkpoint["schema_version"] == NLH_CHECKPOINT_VERSION
    assert serialize_nlh_hand_state(restored) == checkpoint
    assert project_player_hand_state(original, "alice") == project_player_hand_state(restored, "alice")
    assert project_player_hand_state(original, "bob") == project_player_hand_state(restored, "bob")

    action = PokerAction(original.actor_player_id, PokerActionKind.CALL)
    original_next = apply_nlh_action(original, action).state
    restored_next = apply_nlh_action(restored, action).state
    assert serialize_nlh_hand_state(original_next) == serialize_nlh_hand_state(restored_next)


def test_completed_hand_checkpoint_preserves_outcome() -> None:
    original = make_started_hand()
    completed = apply_nlh_action(
        original,
        PokerAction(original.actor_player_id, PokerActionKind.FOLD),
    ).state
    assert completed.outcome is not None

    restored = deserialize_nlh_hand_state(serialize_nlh_hand_state(completed))

    assert restored.outcome == completed.outcome
    assert serialize_nlh_hand_state(restored) == serialize_nlh_hand_state(completed)


def test_checkpoint_rejects_unsupported_version_and_inconsistent_cards() -> None:
    checkpoint = serialize_nlh_hand_state(make_started_hand())
    wrong_version = dict(checkpoint, schema_version=NLH_CHECKPOINT_VERSION + 1)
    with pytest.raises(ValueError, match="Unsupported NLH checkpoint version"):
        deserialize_nlh_hand_state(wrong_version)

    bad_cards = json.loads(json.dumps(checkpoint))
    bad_cards["community_cards"] = [bad_cards["players"][0]["hole_cards"][0]]
    with pytest.raises(ValueError, match="duplicate hole or community cards"):
        deserialize_nlh_hand_state(bad_cards)