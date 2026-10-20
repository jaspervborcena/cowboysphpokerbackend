from logic.first_party_table import FirstPartyNLHGame, _attribute_rake_by_contribution
from game_engine.nlh_state import NLHStreet


def make_game() -> FirstPartyNLHGame:
    game = FirstPartyNLHGame("table-1", 50, 100, 100, 1000)
    assert game.add_player(0, "player-a", "Alice", 500)
    assert game.add_player(1, "player-b", "Bob", 500)
    return game


def test_first_party_table_preserves_snapshot_privacy_and_settlement_contract() -> None:
    game = FirstPartyNLHGame("owned-table", 50, 100, 100, 1000)
    assert game.add_player(0, "player-a", "Alice", 500)
    assert game.add_player(1, "player-b", "Bob", 500)

    assert game.start_new_hand()
    actor = game.current_actor_user_id
    assert actor is not None
    other = "player-b" if actor == "player-a" else "player-a"
    actor_view = game.get_masked_state_for_player(actor)
    other_view = game.get_masked_state_for_player(other)
    actor_cards = next(
        player["hole_cards"] for player in actor_view["players"]
        if player["user_id"] == actor
    )
    masked_cards = next(
        player["hole_cards"] for player in other_view["players"]
        if player["user_id"] == actor
    )
    assert len(actor_cards) == 2
    assert masked_cards == ["??", "??"]

    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()
    assert summary is not None
    assert summary["is_showdown"] is False
    assert summary["frames"][-1]["last_action"]["action"] == "FOLD"
    assert summary["settlement"]["pot_before_fees"] == summary["pot_total"]
    assert sum(player["final_stack"] for player in summary["players"]) == 1000


def test_first_party_showdown_records_a_replay_safe_completed_board() -> None:
    from app.api.hand_history import filter_replay_for_user

    game = FirstPartyNLHGame("showdown-table", 10, 20, 100, 1000)
    assert game.add_player(0, "player-a", "Alice", 500)
    assert game.add_player(1, "player-b", "Bob", 500)
    assert game.start_new_hand()

    while game.is_hand_running:
        actor = game.current_actor_user_id
        assert actor is not None
        action = "CALL" if (game.current_call_amount or 0) > 0 else "CHECK"
        assert game.handle_player_action(actor, action)

    summary = game.pop_completed_hand()
    assert summary is not None
    assert summary["is_showdown"] is True
    assert len(summary["final_board"]) == 5
    assert sum(player["final_stack"] for player in summary["players"]) == 1000

    replay = filter_replay_for_user(summary, "player-a")
    assert replay["total_frames"] == len(summary["frames"])
    for frame in replay["frames"]:
        assert all(len(player["hole_cards"]) == 2 for player in frame["players"])


def test_first_party_table_keeps_waiting_button_player_out_of_hand() -> None:
    game = FirstPartyNLHGame("waiting-button", 5, 10, 100, 1000, max_seats=6)
    assert game.add_player(0, "new-player", "New", 100)
    assert game.add_player(2, "alice", "Alice", 100)
    assert game.add_player(4, "bob", "Bob", 100)
    game.seats[0].new_to_table = True

    assert game.start_new_hand()
    assert {player.player_id for player in game.poker_game.players} == {"alice", "bob"}
    assert game.poker_game.button_seat == 0
    new_player = next(
        player for player in game.get_masked_state_for_player("alice")["players"]
        if player["user_id"] == "new-player"
    )
    assert new_player["waiting_for_big_blind"] is True
    assert new_player["hole_cards"] == []


def test_first_party_table_passes_big_blind_consent_and_straddle() -> None:
    game = FirstPartyNLHGame("straddle-table", 10, 20, 100, 1000, max_seats=6)
    for seat, player_id in enumerate(("button", "small", "big", "utg")):
        assert game.add_player(seat, player_id, player_id, 200)
    game.seats[3].new_to_table = True
    game.seats[3].accept_big_blind_post = True

    assert game.start_new_hand(straddle_player_id="utg")
    utg = next(player for player in game.poker_game.players if player.player_id == "utg")
    assert utg.total_contribution == 60
    assert game.seats[3].new_to_table is False
    assert game.seats[3].accept_big_blind_post is False
    assert any(
        event.kind.value == "STRADDLE_POSTED" and event.player_id == "utg"
        for event in game._engine_events
    )


def test_first_party_audit_records_uncalled_return_separately_from_awards() -> None:
    game = FirstPartyNLHGame("uncalled-table", 50, 100, 100, 1000)
    assert game.add_player(0, "raiser", "Raiser", 500)
    assert game.add_player(1, "caller", "Caller", 500)
    assert game.start_new_hand()

    assert game.handle_player_action("raiser", "RAISE", 500)
    assert game.handle_player_action("caller", "FOLD")

    hand = game.peek_completed_hand()
    assert hand is not None
    assert hand["pot_total"] == 200
    assert hand["uncalled_returns"] == [{"user_id": "raiser", "amount": 400}]
    assert {player["user_id"]: player["net_delta"] for player in hand["players"]} == {
        "raiser": 100,
        "caller": -100,
    }


def test_start_hand_and_mask_opponents_hole_cards() -> None:
    game = make_game()

    assert game.start_new_hand()
    actor_user_id = game.current_actor_user_id
    assert actor_user_id is not None

    actor_view = game.get_masked_state_for_player(actor_user_id)
    other_view = game.get_masked_state_for_player(
        "player-b" if actor_user_id == "player-a" else "player-a"
    )
    actor_cards = next(player["hole_cards"] for player in actor_view["players"] if player["user_id"] == actor_user_id)
    opponent_cards = next(
        player["hole_cards"]
        for player in other_view["players"]
        if player["user_id"] == actor_user_id
    )

    assert len(actor_cards) == 2
    assert opponent_cards == ["??", "??"]


def test_player_home_club_is_included_in_table_snapshot() -> None:
    game = FirstPartyNLHGame("union-table", 10, 20, 20, 1000)
    assert game.add_player(0, "player-a", "Alice", 100, club_id="club-manila")

    snapshot = game.get_masked_state_for_player("player-a")

    assert snapshot["players"][0]["club_id"] == "club-manila"


def test_wrong_player_cannot_act_and_fold_completes_hand() -> None:
    game = make_game()
    assert game.start_new_hand()
    actor = game.current_actor_user_id
    assert actor is not None
    other = "player-b" if actor == "player-a" else "player-a"

    try:
        game.handle_player_action(other, "FOLD")
    except ValueError as error:
        assert "turn" in str(error)
    else:
        raise AssertionError("An out-of-turn action should be rejected.")

    assert game.handle_player_action(actor, "FOLD")
    assert not game.is_hand_running
    summary = game.pop_completed_hand()
    assert summary is not None
    assert {player["net_delta"] for player in summary["players"]} == {-50, 50}
    assert sum(player["net_delta"] for player in summary["players"]) == -summary["settlement"]["house_take"]


def test_completed_hand_keeps_private_cards_and_records_action_frames() -> None:
    game = make_game()
    assert game.start_new_hand()
    initial_frame = game._replay_frames[0]
    assert len(initial_frame["players"]) == 2
    dealt_cards = {
        player["user_id"]: player["hole_cards"]
        for player in initial_frame["players"]
    }

    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()

    assert summary is not None
    assert summary["is_showdown"] is False
    assert len(summary["frames"]) == 2
    assert summary["frames"][1]["last_action"]["action"] == "FOLD"
    assert summary["frames"][1]["last_action"]["timestamp"]
    assert {
        player["user_id"]: player["hole_cards"] for player in summary["players"]
    } == dealt_cards
    assert summary["deck_state"]["remaining_cards"]


def test_jackpot_fee_is_accounted_for_when_enabled_and_eligible() -> None:
    game = make_game()
    game.update_table_settings(
        jackpot_enabled=True,
        jackpot_fee=25,
        jackpot_min_pot=100,
        jackpot_qualifying_hand="quads-or-better",
    )
    assert game.start_new_hand()

    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()

    assert summary is not None
    assert summary["jackpot"]["eligible"] is True
    assert summary["jackpot"]["fee_collected"] == 25
    assert summary["jackpot"]["pool_after_hand"] == 25
    assert summary["jackpot"]["qualifying_hand"] == "quads-or-better"


def test_rake_and_hand_of_day_are_calculated_from_table_settings() -> None:
    game = make_game()
    game.update_table_settings(
        rake_rate=0.05,
        rake_cap=50,
        rake_min_pot=100,
        hand_of_day_enabled=True,
        hand_of_day_target=500,
        hand_of_day_prize=100,
    )

    rake = game.calculate_rake_accounting(1000)
    hotd = game.calculate_hand_of_day_accounting(1000)

    assert rake["eligible"] is True
    assert rake["rake_amount"] == 50
    assert rake["pool_after_hand"] == 50
    assert hotd["eligible"] is True
    assert hotd["prize_amount"] == 100
    assert hotd["target_pot"] == 500


def test_no_flop_no_drop_disables_preflop_rake() -> None:
    game = make_game()
    game.update_table_settings(rake_rate=0.10, rake_min_pot=0, no_flop_no_drop=True)

    rake = game.calculate_rake_accounting(1000, dealt_flop=False)

    assert rake["eligible"] is False
    assert rake["rake_amount"] == 0


def test_rake_attribution_is_contribution_weighted_and_conserves_chips() -> None:
    rake_shares = _attribute_rake_by_contribution(
        100,
        {"player-a": 500, "player-b": 300, "player-c": 200},
        ("player-a", "player-b", "player-c"),
    )

    assert rake_shares == {"player-a": 50, "player-b": 30, "player-c": 20}
    assert sum(rake_shares.values()) == 100


def test_rake_attribution_uses_seat_order_to_break_rounding_ties() -> None:
    rake_shares = _attribute_rake_by_contribution(
        2,
        {"player-a": 1, "player-b": 1, "player-c": 1},
        ("player-c", "player-a", "player-b"),
    )

    assert rake_shares == {"player-a": 1, "player-b": 0, "player-c": 1}


def test_completed_hand_records_each_players_contribution_rake_and_result() -> None:
    game = make_game()
    game.update_table_settings(
        rake_rate=0.05,
        rake_min_pot=0,
        no_flop_no_drop=False,
    )
    assert game.start_new_hand()

    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()

    assert summary is not None
    assert sum(player["contestable_contribution"] for player in summary["players"]) == summary["pot_total"]
    assert sum(player["rake_attributed"] for player in summary["players"]) == summary["rake_amount"]
    assert sum(player["gross_award"] for player in summary["players"]) == summary["pot_total"]
    assert sum(player["payout_after_fees"] for player in summary["players"]) == summary["settlement"]["player_payout_total"]
    for player in summary["players"]:
        assert player["net_delta"] == player["final_stack"] - player["starting_stack"]


def test_preflop_folders_are_excluded_but_turn_folders_keep_rake_attribution() -> None:
    game = FirstPartyNLHGame("rake-fold-eligibility", 50, 100, 100, 1000, max_seats=3)
    for seat, player_id in enumerate(("player-a", "player-b", "player-c")):
        assert game.add_player(seat, player_id, player_id, 500)
    game.update_table_settings(
        rake_rate=0.10,
        rake_min_pot=0,
        no_flop_no_drop=True,
    )
    assert game.start_new_hand()

    while game.is_hand_running:
        state = game.poker_game
        actor = game.current_actor_user_id
        assert actor is not None
        if state.street is NLHStreet.PREFLOP and actor == "player-a":
            action = "FOLD"
        elif state.street is NLHStreet.TURN and actor == "player-b":
            action = "FOLD"
        else:
            action = "CALL" if (game.current_call_amount or 0) > 0 else "CHECK"
        assert game.handle_player_action(actor, action)

    summary = game.pop_completed_hand()
    assert summary is not None
    players = {player["user_id"]: player for player in summary["players"]}

    assert summary["rake_amount"] > 0
    assert players["player-a"]["rake_eligible"] is False
    assert players["player-a"]["rake_attributed"] == 0
    assert players["player-b"]["rake_eligible"] is True
    assert players["player-b"]["rake_attributed"] > 0
    assert players["player-c"]["rake_eligible"] is True
    assert sum(player["rake_attributed"] for player in players.values()) == summary["rake_amount"]


def test_pre_action_is_cleared_when_hand_ends_and_next_hand_starts() -> None:
    game = make_game()
    assert game.start_new_hand()
    actor = game.current_actor_user_id
    assert actor is not None
    waiting_player = "player-b" if actor == "player-a" else "player-a"

    assert game.set_pre_action(waiting_player, "CALL_ANY") == "CALL_ANY"
    assert game.get_pre_action_for_player(waiting_player)["action"] == "CALL_ANY"
    assert game.handle_player_action(actor, "FOLD")
    assert game.start_new_hand()
    assert game.get_pre_action_for_player(waiting_player)["action"] is None


def test_hand_summary_applies_rake_and_jackpot_before_player_payouts() -> None:
    game = make_game()
    game.update_table_settings(
        jackpot_enabled=True,
        jackpot_fee=25,
        rake_rate=0.10,
        rake_cap=50,
        rake_min_pot=0,
        no_flop_no_drop=False,
    )
    assert game.start_new_hand()

    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()

    assert summary is not None
    assert summary["rake_amount"] == 10
    assert summary["jackpot"]["fee_collected"] == 25
    assert summary["settlement"]["house_take"] == 35
    assert summary["settlement"]["player_payout_total"] == summary["pot_total"] - 35
    assert sum(player["net_delta"] for player in summary["players"]) == -35
    assert sum(player["final_stack"] for player in summary["players"]) == 965


def test_rake_and_jackpot_drops_never_exceed_the_completed_pot() -> None:
    game = make_game()
    game.update_table_settings(
        jackpot_enabled=True,
        jackpot_fee=500,
        jackpot_min_pot=0,
        rake_rate=0.10,
        rake_cap=100,
        rake_min_pot=0,
        no_flop_no_drop=False,
    )
    assert game.start_new_hand()

    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()

    assert summary is not None
    assert summary["rake_amount"] + summary["jackpot"]["fee_collected"] == summary["pot_total"]
    assert summary["settlement"]["house_take"] == summary["pot_total"]
    assert sum(player["net_delta"] for player in summary["players"]) == -summary["pot_total"]


def test_hand_summary_reserves_hand_of_day_prize_before_player_payouts() -> None:
    game = make_game()
    game.update_table_settings(
        rake_rate=0.10,
        rake_cap=50,
        rake_min_pot=0,
        no_flop_no_drop=False,
        hand_of_day_enabled=True,
        hand_of_day_target=10,
        hand_of_day_prize=100,
    )
    assert game.start_new_hand()

    actor = game.current_actor_user_id
    assert actor is not None
    assert game.handle_player_action(actor, "FOLD")
    summary = game.pop_completed_hand()

    assert summary is not None
    assert summary["hand_of_the_day"]["eligible"] is True
    assert summary["settlement"]["house_take"] == 10
    assert summary["settlement"]["player_payout_total"] == summary["pot_total"] - 10