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