from __future__ import annotations

import os
from uuid import uuid4

import pytest
from google.cloud import firestore

from database.hand_repository import FirestoreHandRepository
from logic.first_party_table import FirstPartyNLHGame


@pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Run with firebase emulators:exec --only firestore.",
)
def test_nlh_uncalled_return_settlement_is_atomic_and_idempotent() -> None:
    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or "demo-cowboys-ph-poker"
    )
    client = firestore.Client(project=project_id)
    repository = FirestoreHandRepository(client)
    suffix = uuid4().hex
    club_id = f"emulator-club-{suffix}"
    table_id = f"emulator-table-{suffix}"
    player_ids = (f"player-a-{suffix}", f"player-b-{suffix}")

    game = FirstPartyNLHGame(table_id, 50, 100, 100, 1000)
    for seat, player_id in enumerate(player_ids):
        assert game.add_player(seat, player_id, player_id, 500, club_id=club_id)
    assert game.start_new_hand()

    raiser = game.current_actor_user_id
    assert raiser == player_ids[0]
    assert game.handle_player_action(raiser, "RAISE", 500)
    caller = game.current_actor_user_id
    assert caller == player_ids[1]
    assert game.handle_player_action(caller, "FOLD")

    hand_summary = game.peek_completed_hand()
    assert hand_summary is not None
    assert hand_summary["uncalled_returns"] == [
        {"user_id": raiser, "amount": 400}
    ]
    chip_deltas = {
        player["user_id"]: player["net_delta"]
        for player in hand_summary["players"]
    }

    for player_id in player_ids:
        client.collection("clubs").document(club_id).collection("members").document(
            player_id
        ).set({"chip_balance": 0, "in_play_balance": 500})
    hand_ref = (
        client.collection("clubs")
        .document(club_id)
        .collection("tables")
        .document(table_id)
        .collection("hands")
        .document(hand_summary["hand_id"])
    )

    assert repository.persist_hand_history_and_ledgers(
        club_id, table_id, hand_summary, chip_deltas
    )
    first_balances = {
        player_id: client.collection("clubs")
        .document(club_id)
        .collection("members")
        .document(player_id)
        .get()
        .to_dict()["in_play_balance"]
        for player_id in player_ids
    }
    assert first_balances == {
        player_ids[0]: 600,
        player_ids[1]: 400,
    }
    assert hand_ref.get().to_dict()["settlement_status"] == "settled"
    assert hand_ref.get().to_dict()["uncalled_returns"] == [
        {"user_id": raiser, "amount": 400}
    ]

    assert not repository.persist_hand_history_and_ledgers(
        club_id, table_id, hand_summary, chip_deltas
    )
    retry_balances = {
        player_id: client.collection("clubs")
        .document(club_id)
        .collection("members")
        .document(player_id)
        .get()
        .to_dict()["in_play_balance"]
        for player_id in player_ids
    }
    assert retry_balances == first_balances