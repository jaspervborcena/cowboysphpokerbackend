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
        ).set({"available_balance": 0, "in_play_balance": 500})
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


@pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Run with firebase emulators:exec --only firestore.",
)
def test_nlh_rake_attribution_settlement_is_atomic_and_idempotent() -> None:
    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or "demo-cowboys-ph-poker"
    )
    client = firestore.Client(project=project_id)
    repository = FirestoreHandRepository(client)
    suffix = uuid4().hex
    club_id = f"emulator-rake-club-{suffix}"
    table_id = f"emulator-rake-table-{suffix}"
    player_ids = (f"rake-player-a-{suffix}", f"rake-player-b-{suffix}")

    game = FirstPartyNLHGame(table_id, 50, 100, 100, 1000)
    for seat, player_id in enumerate(player_ids):
        assert game.add_player(seat, player_id, player_id, 500, club_id=club_id)
    game.update_table_settings(
        rake_rate=0.10,
        rake_min_pot=0,
        no_flop_no_drop=False,
    )
    assert game.start_new_hand()
    folder = game.current_actor_user_id
    assert folder == player_ids[0]
    assert game.handle_player_action(folder, "FOLD")

    hand_summary = game.peek_completed_hand()
    assert hand_summary is not None
    assert hand_summary["rake_amount"] > 0
    chip_deltas = {
        player["user_id"]: player["net_delta"]
        for player in hand_summary["players"]
    }
    for player_id in player_ids:
        client.collection("clubs").document(club_id).collection("members").document(
            player_id
        ).set({"available_balance": 0, "in_play_balance": 500})

    assert repository.persist_hand_history_and_ledgers(
        club_id, table_id, hand_summary, chip_deltas
    )
    pool_ref = (
        client.collection("clubs")
        .document(club_id)
        .collection("treasury")
        .document("pools")
    )
    assert pool_ref.get().to_dict()["total_rake_collected"] == hand_summary["rake_amount"]

    rake_records = []
    for player_id in player_ids:
        rake_stats_ref = (
            client.collection("clubs")
            .document(club_id)
            .collection("player_rake_stats")
            .document(player_id)
        )
        stats = rake_stats_ref.get().to_dict()
        record = (
            rake_stats_ref.collection("hands")
            .document(hand_summary["hand_id"])
            .get()
            .to_dict()
        )
        assert stats["hands_played"] == 1
        rake_records.append(record)

    records_by_player = {record["user_id"]: record for record in rake_records}
    assert records_by_player[player_ids[0]]["rake_eligible"] is False
    assert records_by_player[player_ids[0]]["rake_attributed"] == 0
    assert records_by_player[player_ids[1]]["rake_eligible"] is True
    assert records_by_player[player_ids[1]]["rake_attributed"] == hand_summary["rake_amount"]
    assert sum(record["rake_deduction"] for record in rake_records) == hand_summary[
        "rake_amount"
    ]
    assert sum(record["bbj_drop_deduction"] for record in rake_records) == hand_summary[
        "jackpot"
    ]["fee_collected"]
    for record in rake_records:
        assert record["payout_after_fees"] == (
            record["gross_award"]
            - record["rake_deduction"]
            - record["bbj_drop_deduction"]
        )

    assert not repository.persist_hand_history_and_ledgers(
        club_id, table_id, hand_summary, chip_deltas
    )
    assert pool_ref.get().to_dict()["total_rake_collected"] == hand_summary["rake_amount"]
    for player_id in player_ids:
        stats = (
            client.collection("clubs")
            .document(club_id)
            .collection("player_rake_stats")
            .document(player_id)
            .get()
            .to_dict()
        )
        assert stats["hands_played"] == 1


@pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Run with firebase emulators:exec --only firestore.",
)
def test_bbj_award_pool_and_credits_are_atomic_and_idempotent() -> None:
    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or "demo-cowboys-ph-poker"
    )
    client = firestore.Client(project=project_id)
    repository = FirestoreHandRepository(client)
    suffix = uuid4().hex
    club_id = f"emulator-bbj-club-{suffix}"
    table_id = f"emulator-bbj-table-{suffix}"
    hand_id = f"emulator-bbj-hand-{suffix}"
    player_ids = ("loser", "winner", "table-player", "preflop-folder")
    pool_ref = (
        client.collection("clubs")
        .document(club_id)
        .collection("treasury")
        .document("pools")
    )
    pool_ref.set({"bad_beat_jackpot_pool": 100000})
    for user_id in player_ids:
        client.collection("clubs").document(club_id).collection("members").document(
            f"{user_id}-{suffix}"
        ).set({"available_balance": 10, "in_play_balance": 100})

    players = [
        {
            "seat": seat,
            "user_id": f"{user_id}-{suffix}",
            "username": user_id,
            "starting_stack": 100,
            "final_stack": 100,
            "contestable_contribution": 0,
            "uncalled_return": 0,
            "gross_award": 0,
            "payout_after_fees": 0,
            "rake_attributed": 0,
            "rake_eligible": user_id != "preflop-folder",
            "net_delta": 0,
        }
        for seat, user_id in enumerate(player_ids)
    ]
    summary = {
        "hand_id": hand_id,
        "table_id": table_id,
        "big_blind": 100,
        "rake_amount": 0,
        "jackpot": {
            "fee_collected": 0,
            "qualification": {
                "game_id": "no_limit_texas_holdem",
                "rule_version": "nlh-bbj-aces-full-v1",
                "eligible": True,
                "reason_code": "QUALIFIED",
                "qualifying_losers": [{"user_id": players[0]["user_id"], "seat": 0}],
                "qualifying_winners": [{"user_id": players[1]["user_id"], "seat": 1}],
            },
        },
        "timestamp": "2026-10-20T12:00:00+00:00",
        "players": players,
        "winners": [],
        "uncalled_returns": [],
        "frames": [],
        "pot_distributions": [],
    }
    chip_deltas = {player["user_id"]: 0 for player in players}

    assert repository.persist_hand_history_and_ledgers(
        club_id, table_id, summary, chip_deltas
    )
    assert not repository.persist_hand_history_and_ledgers(
        club_id, table_id, summary, chip_deltas
    )

    assert pool_ref.get().to_dict()["bad_beat_jackpot_pool"] == 90000
    assert client.collection("clubs").document(club_id).collection(
        "transactions"
    ).document(f"{hand_id}_bbj_award").get().to_dict()["amount"] == 10000
    for user_id, expected_available_balance, expected_in_play_balance in (
        (players[0]["user_id"], 10, 5100),
        (players[1]["user_id"], 10, 2600),
        (players[2]["user_id"], 10, 2600),
        (players[3]["user_id"], 10, 100),
    ):
        member = client.collection("clubs").document(club_id).collection(
            "members"
        ).document(user_id).get().to_dict()
        assert member["available_balance"] == expected_available_balance
        assert member["in_play_balance"] == expected_in_play_balance


@pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Run with firebase emulators:exec --only firestore.",
)
def test_rake_player_report_uses_aggregate_total_and_composite_pages() -> None:
    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or "demo-cowboys-ph-poker"
    )
    client = firestore.Client(project=project_id)
    repository = FirestoreHandRepository(client)
    suffix = uuid4().hex
    club_id = f"emulator-report-club-{suffix}"
    club_ref = client.collection("clubs").document(club_id)
    player_totals = (("player-a", 8), ("player-b", 8), ("player-c", 4))

    club_ref.collection("treasury").document("pools").set(
        {"total_rake_collected": 20}
    )
    for player_id, rake_total in player_totals:
        club_ref.collection("player_rake_stats").document(player_id).set(
            {"user_id": player_id, "total_rake_generated": rake_total}
        )

    first_page = repository.get_club_player_rake_report(club_id, limit=2)
    second_page = repository.get_club_player_rake_report(
        club_id,
        limit=2,
        before_total_rake_generated=first_page[
            "next_before_total_rake_generated"
        ],
        before_user_id=first_page["next_before_user_id"],
    )

    assert first_page["attributed_rake_total"] == 20
    assert [player["user_id"] for player in first_page["players"]] == [
        "player-a", "player-b",
    ]
    assert [player["user_id"] for player in second_page["players"]] == ["player-c"]