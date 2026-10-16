import pytest
from dataclasses import replace

from game_engine import (
    Deck,
    NLHStreet,
    PlayerSeed,
    PokerTableConfig,
    advance_nlh_street,
    begin_nlh_showdown,
    initialize_nlh_hand,
    project_player_hand_state,
)


def make_config(max_seats: int = 6) -> PokerTableConfig:
    return PokerTableConfig("no_limit_texas_holdem", 5, 10, max_seats)


def test_initial_hand_deals_heads_up_button_first_and_projects_private_cards() -> None:
    deck = Deck.for_testing(31)
    state = initialize_nlh_hand(
        "table-a",
        "hand-a",
        make_config(),
        (
            PlayerSeed("alice", 1, 500, "Alice"),
            PlayerSeed("bob", 4, 500, "Bob"),
        ),
        button_seat=1,
        deck=deck,
    )

    dealt = deck.dealt_cards
    alice = next(player for player in state.players if player.player_id == "alice")
    bob = next(player for player in state.players if player.player_id == "bob")
    assert alice.hole_cards == (dealt[0], dealt[2])
    assert bob.hole_cards == (dealt[1], dealt[3])
    assert state.street is NLHStreet.PREFLOP

    alice_view = project_player_hand_state(state, "alice")
    bob_view = project_player_hand_state(state, "bob")
    assert alice_view["players"][0]["hole_cards"] == [card.code for card in alice.hole_cards]
    assert bob_view["players"][0]["hole_cards"] == ["??", "??"]


def test_multiway_deal_starts_left_of_button_and_wraps_seat_ring() -> None:
    deck = Deck.for_testing(32)
    state = initialize_nlh_hand(
        "table-a",
        "hand-b",
        make_config(),
        (
            PlayerSeed("alice", 0, 500),
            PlayerSeed("bob", 2, 500),
            PlayerSeed("carol", 5, 500),
        ),
        button_seat=2,
        deck=deck,
    )

    dealt = deck.dealt_cards
    by_user = {player.player_id: player for player in state.players}
    assert by_user["carol"].hole_cards == (dealt[0], dealt[3])
    assert by_user["alice"].hole_cards == (dealt[1], dealt[4])
    assert by_user["bob"].hole_cards == (dealt[2], dealt[5])


def test_public_reveal_is_visible_to_opponents_after_state_update() -> None:
    state = initialize_nlh_hand(
        "table-a",
        "hand-c",
        make_config(),
        (PlayerSeed("alice", 1, 500), PlayerSeed("bob", 4, 500)),
        button_seat=1,
        deck=Deck.for_testing(33),
    )
    revealed_state = state.reveal_hole_cards({"alice"})

    view = project_player_hand_state(revealed_state, "bob")
    alice_view = next(player for player in view["players"] if player["user_id"] == "alice")
    alice_state = next(player for player in state.players if player.player_id == "alice")
    assert alice_view["hole_cards"] == [card.code for card in alice_state.hole_cards]


def test_street_advancement_deals_flop_turn_and_river_in_order() -> None:
    deck = Deck.for_testing(37)
    state = initialize_nlh_hand(
        "table-a",
        "hand-streets",
        make_config(),
        (PlayerSeed("alice", 1, 500), PlayerSeed("bob", 4, 500)),
        button_seat=1,
        deck=deck,
    )

    state = advance_nlh_street(state)
    assert state.street is NLHStreet.FLOP
    assert len(state.community_cards) == 3
    assert len(deck.burned_cards) == 1

    state = advance_nlh_street(state)
    assert state.street is NLHStreet.TURN
    assert len(state.community_cards) == 4
    assert len(deck.burned_cards) == 2

    state = advance_nlh_street(state)
    assert state.street is NLHStreet.RIVER
    assert len(state.community_cards) == 5
    assert len(deck.burned_cards) == 3
    assert len(set((*state.community_cards, *(card for player in state.players for card in player.hole_cards), *deck.burned_cards))) == 12

    with pytest.raises(ValueError, match="Only preflop, flop, and turn"):
        advance_nlh_street(state)


def test_showdown_reveals_live_players_but_keeps_folded_cards_hidden() -> None:
    state = initialize_nlh_hand(
        "table-a",
        "hand-showdown",
        make_config(),
        (
            PlayerSeed("alice", 1, 500),
            PlayerSeed("bob", 3, 500),
            PlayerSeed("carol", 4, 500),
        ),
        button_seat=1,
        deck=Deck.for_testing(38),
    )
    state = advance_nlh_street(advance_nlh_street(advance_nlh_street(state)))
    state = replace(
        state,
        players=tuple(
            replace(player, folded=player.player_id == "bob")
            for player in state.players
        ),
    )

    with pytest.raises(ValueError, match="fully dealt river board"):
        begin_nlh_showdown(initialize_nlh_hand(
            "table-a",
            "hand-early-showdown",
            make_config(),
            (PlayerSeed("alice", 1, 500), PlayerSeed("bob", 4, 500)),
            button_seat=1,
            deck=Deck.for_testing(39),
        ))

    state = begin_nlh_showdown(state)
    assert state.street is NLHStreet.SHOWDOWN
    view = project_player_hand_state(state, "alice")
    players = {player["user_id"]: player for player in view["players"]}
    assert players["bob"]["hole_cards"] == ["??", "??"]
    carol_cards = next(player.hole_cards for player in state.players if player.player_id == "carol")
    assert players["carol"]["hole_cards"] == [card.code for card in carol_cards]


def test_projection_rejects_unseated_viewer_and_reveal_of_unknown_player() -> None:
    state = initialize_nlh_hand(
        "table-a",
        "hand-d",
        make_config(),
        (PlayerSeed("alice", 1, 500), PlayerSeed("bob", 4, 500)),
        button_seat=1,
        deck=Deck.for_testing(34),
    )

    with pytest.raises(ValueError, match="Viewer must be seated"):
        project_player_hand_state(state, "mallory")
    with pytest.raises(ValueError, match="Only seated players"):
        state.reveal_hole_cards({"mallory"})


def test_hand_initialization_rejects_unseated_button_and_duplicate_seats() -> None:
    config = make_config()
    players = (PlayerSeed("alice", 1, 500), PlayerSeed("bob", 4, 500))

    with pytest.raises(ValueError, match="Button"):
        initialize_nlh_hand("table-a", "hand-e", config, players, 3, Deck.for_testing(35))
    with pytest.raises(ValueError, match="unique"):
        initialize_nlh_hand(
            "table-a",
            "hand-f",
            config,
            (PlayerSeed("alice", 1, 500), PlayerSeed("bob", 1, 500)),
            1,
            Deck.for_testing(36),
        )