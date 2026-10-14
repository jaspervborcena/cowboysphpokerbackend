from base_poker_game import BasePokerGame


def make_game() -> BasePokerGame:
    game = BasePokerGame("table-1", 50, 100, 100, 1000)
    assert game.add_player(0, "player-a", "Alice", 500)
    assert game.add_player(1, "player-b", "Bob", 500)
    return game


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
    game = BasePokerGame("union-table", 10, 20, 20, 1000)
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
    assert summary["rake_amount"] == 15
    assert summary["jackpot"]["fee_collected"] == 25
    assert summary["settlement"]["house_take"] == 40
    assert summary["settlement"]["player_payout_total"] == summary["pot_total"] - 40
    assert sum(player["net_delta"] for player in summary["players"]) == -40
    assert sum(player["final_stack"] for player in summary["players"]) == 960


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
    assert summary["settlement"]["house_take"] == 15
    assert summary["settlement"]["player_payout_total"] == summary["pot_total"] - 15