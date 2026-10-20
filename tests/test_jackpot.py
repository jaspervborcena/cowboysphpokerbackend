from game_engine.jackpot import (
    BBJ_RULE_VERSION,
    calculate_bad_beat_award,
    evaluate_bad_beat_qualification,
    evaluate_hand_of_day,
)


def qualifying_players() -> list[dict[str, object]]:
    return [
        {
            "user_id": "loser",
            "seat": 0,
            "hole_cards": ["Kh", "Qh"],
            "folded": False,
            "rake_eligible": True,
        },
        {
            "user_id": "winner",
            "seat": 1,
            "hole_cards": ["As", "2c"],
            "folded": False,
            "rake_eligible": True,
        },
        {
            "user_id": "table-player",
            "seat": 2,
            "hole_cards": ["3h", "4h"],
            "folded": True,
            "rake_eligible": True,
        },
        {
            "user_id": "preflop-folder",
            "seat": 3,
            "hole_cards": ["5h", "6h"],
            "folded": True,
            "rake_eligible": False,
        },
    ]


def test_bad_beat_qualifies_aces_full_beaten_by_quads_at_minimum() -> None:
    players = qualifying_players()
    result = evaluate_bad_beat_qualification(
        players,
        ["Ah", "Ad", "Ac", "Ks", "Kd"],
        pot_total=2000,
        big_blind=100,
        is_showdown=True,
        enabled=True,
    )

    assert result["eligible"] is True
    assert result["rule_version"] == BBJ_RULE_VERSION
    assert result["reason_code"] == "QUALIFIED"
    assert [entry["user_id"] for entry in result["qualifying_losers"]] == ["loser"]
    assert [entry["user_id"] for entry in result["qualifying_winners"]] == ["winner"]
    loser_evidence = result["qualifying_losers"][0]
    assert loser_evidence["rank_category"] == "FULL_HOUSE"
    assert len(loser_evidence["hole_cards"]) + len(loser_evidence["board_cards"]) == 5


def test_bad_beat_rejects_non_showdown_small_table_and_low_pot() -> None:
    players = qualifying_players()
    board = ["Ah", "Ad", "Ac", "Ks", "Kd"]
    assert evaluate_bad_beat_qualification(
        players, board, 2000, 100, False, True
    )["reason_code"] == "NOT_SHOWDOWN"
    assert evaluate_bad_beat_qualification(
        players[:3], board, 2000, 100, True, True
    )["reason_code"] == "INSUFFICIENT_DEALT_IN_PLAYERS"
    assert evaluate_bad_beat_qualification(
        players, board, 1999, 100, True, True
    )["reason_code"] == "POT_BELOW_MINIMUM"


def test_bad_beat_requires_aces_full_loser_and_quads_winner() -> None:
    players = qualifying_players()
    players[0]["hole_cards"] = ["Kh", "Jd"]
    players[1]["hole_cards"] = ["Qs", "2c"]
    result = evaluate_bad_beat_qualification(
        players,
        ["Qh", "Qd", "Qc", "Ks", "Kd"],
        pot_total=2000,
        big_blind=100,
        is_showdown=True,
        enabled=True,
    )
    assert result["eligible"] is False
    assert result["reason_code"] == "NO_QUALIFYING_LOSING_HAND"


def test_bad_beat_award_uses_stake_access_and_candidate_split() -> None:
    players = qualifying_players()
    qualification = evaluate_bad_beat_qualification(
        players, ["Ah", "Ad", "Ac", "Ks", "Kd"], 2000, 100, True, True
    )

    award = calculate_bad_beat_award(100_000, 100, players, qualification)

    assert award["configured_pool_access_percent"] == 10
    assert award["award_amount"] == 10_000
    assert award["loser_share"] == 5_000
    assert award["winner_share"] == 2_500
    assert award["table_share"] == 2_500
    assert award["loser_credits"] == {"loser": 5_000}
    assert award["winner_credits"] == {"winner": 2_500}
    assert award["table_credits"] == {"table-player": 2_500}
    assert award["pool_balance_after_award"] == 90_000


def test_bad_beat_table_share_uses_equal_splits_with_seat_order_remainder() -> None:
    players = qualifying_players()
    players.append(
        {
            "user_id": "table-player-later",
            "seat": 5,
            "hole_cards": ["7h", "8h"],
            "folded": True,
            "rake_eligible": True,
        }
    )
    qualification = evaluate_bad_beat_qualification(
        players, ["Ah", "Ad", "Ac", "Ks", "Kd"], 2000, 100, True, True
    )

    award = calculate_bad_beat_award(10_035, 100, players, qualification)

    assert award["award_amount"] == 1003
    assert award["table_share"] == 251
    assert award["table_credits"] == {
        "table-player": 126,
        "table-player-later": 125,
    }
    assert sum(award["loser_credits"].values()) + sum(
        award["winner_credits"].values()
    ) + sum(award["table_credits"].values()) == award["paid_amount"]


def test_bad_beat_returns_unassigned_table_share_to_pool() -> None:
    players = qualifying_players()
    players[2]["rake_eligible"] = False
    players[3]["rake_eligible"] = False
    qualification = evaluate_bad_beat_qualification(
        players, ["Ah", "Ad", "Ac", "Ks", "Kd"], 2000, 100, True, True
    )

    award = calculate_bad_beat_award(100_000, 100, players, qualification)

    assert award["table_share"] == 2_500
    assert award["table_credits"] == {}
    assert award["returned_to_pool"] == 2_500
    assert award["paid_amount"] == 7_500
    assert award["pool_balance_after_award"] == 92_500


def test_bad_beat_award_caps_access_and_preserves_reserve() -> None:
    players = qualifying_players()
    qualification = evaluate_bad_beat_qualification(
        players, ["Ah", "Ad", "Ac", "Ks", "Kd"], 2000, 100, True, True
    )

    award = calculate_bad_beat_award(100_000, 400, players, qualification)

    assert award["effective_pool_access_percent"] == 15
    assert award["award_amount"] == 15_000


def test_bad_beat_interpolates_pool_access_between_stake_points() -> None:
    players = qualifying_players()
    qualification = evaluate_bad_beat_qualification(
        players, ["Ah", "Ad", "Ac", "Ks", "Kd"], 2000, 75, True, True
    )

    award = calculate_bad_beat_award(100_000, 75, players, qualification)

    assert award["configured_pool_access_percent"] == 7.5
    assert award["award_amount"] == 7_500


def test_hand_of_day_records_strongest_quads_or_better_hand() -> None:
    result = evaluate_hand_of_day(
        [
            {
                "user_id": "three-quads",
                "seat": 0,
                "hole_cards": ["3s", "Ac"],
                "folded": False,
            },
            {
                "user_id": "jack-quads",
                "seat": 1,
                "hole_cards": ["Js", "Jc"],
                "folded": False,
            },
        ],
        ["3c", "3d", "3h", "Jd", "Jh"],
        "no_limit_texas_holdem",
        True,
    )

    assert result["eligible"] is True
    assert result["reason_code"] == "QUALIFIED"
    assert result["leaders"][0]["user_id"] == "jack-quads"
    assert result["leaders"][0]["rank_category"] == "FOUR_OF_A_KIND"


def test_hand_of_day_rejects_non_showdown_and_below_quads() -> None:
    players = [
        {
            "user_id": "player",
            "seat": 0,
            "hole_cards": ["As", "Kd"],
            "folded": False,
        }
    ]
    board = ["Ah", "Ac", "Ks", "7d", "2c"]
    assert evaluate_hand_of_day(
        players, board, "no_limit_texas_holdem", False
    )["reason_code"] == "NOT_SHOWDOWN"
    assert evaluate_hand_of_day(
        players, board, "no_limit_texas_holdem", True
    )["reason_code"] == "NO_QUALIFYING_HAND"
    assert evaluate_hand_of_day(
        players, board, "pot_limit_omaha_4", True
    )["reason_code"] == "UNSUPPORTED_GAME"


def test_hand_of_day_returns_equal_best_hands_for_shared_board_quads() -> None:
    result = evaluate_hand_of_day(
        [
            {"user_id": "one", "seat": 0, "hole_cards": ["2c", "3c"]},
            {"user_id": "two", "seat": 1, "hole_cards": ["4c", "5c"]},
        ],
        ["Jh", "Jd", "Jc", "Js", "Ah"],
        "no_limit_texas_holdem",
        True,
    )

    assert {leader["user_id"] for leader in result["leaders"]} == {"one", "two"}
