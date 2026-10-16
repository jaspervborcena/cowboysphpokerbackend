import pytest

from game_engine import (
    HandOutcome,
    PlayerAward,
    PlayerSeed,
    PokerAction,
    PokerActionKind,
    PokerEvent,
    PokerEventKind,
    PokerGameMode,
    PokerTableConfig,
)


def test_table_config_accepts_valid_nlh_blinds_and_capacity() -> None:
    config = PokerTableConfig(
        game_id="no_limit_texas_holdem",
        small_blind=50,
        big_blind=100,
        max_seats=9,
    )

    assert config.game_id == "no_limit_texas_holdem"
    assert config.game_mode is PokerGameMode.RING


def test_table_config_can_select_separate_tournament_mode() -> None:
    config = PokerTableConfig(
        game_id="no_limit_texas_holdem",
        small_blind=50,
        big_blind=100,
        max_seats=9,
        game_mode=PokerGameMode.TOURNAMENT,
    )
    assert config.game_mode is PokerGameMode.TOURNAMENT


@pytest.mark.parametrize(
    "values",
    [
        {"game_id": "", "small_blind": 1, "big_blind": 2, "max_seats": 2},
        {"game_id": "nlh", "small_blind": 0, "big_blind": 2, "max_seats": 2},
        {"game_id": "nlh", "small_blind": 3, "big_blind": 2, "max_seats": 2},
        {"game_id": "nlh", "small_blind": 1, "big_blind": 2, "max_seats": 1},
    ],
)
def test_table_config_rejects_invalid_values(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PokerTableConfig(**values)  # type: ignore[arg-type]


def test_player_seed_and_raise_action_are_explicit() -> None:
    player = PlayerSeed("player-a", seat_index=2, starting_stack=500)
    action = PokerAction("player-a", PokerActionKind.RAISE, raise_to=300)

    assert player.starting_stack == 500
    assert action.raise_to == 300


def test_only_new_players_can_accept_a_big_blind_entry_post() -> None:
    player = PlayerSeed(
        "new-player",
        seat_index=2,
        starting_stack=500,
        new_to_table=True,
        accept_big_blind_post=True,
    )
    assert player.accept_big_blind_post

    with pytest.raises(ValueError, match="Only a new player"):
        PlayerSeed(
            "player-a",
            seat_index=2,
            starting_stack=500,
            accept_big_blind_post=True,
        )


def test_non_raise_action_cannot_carry_raise_amount() -> None:
    with pytest.raises(ValueError, match="Only a raise"):
        PokerAction("player-a", PokerActionKind.CALL, raise_to=300)


def test_hand_outcome_requires_gross_awards_to_balance_contestable_pot() -> None:
    outcome = HandOutcome(
        total_pot=101,
        gross_awards=(PlayerAward("player-a", 51), PlayerAward("player-b", 50)),
        uncalled_returns=(PlayerAward("player-a", 25),),
    )

    assert sum(award.amount for award in outcome.gross_awards) == outcome.total_pot
    assert outcome.uncalled_returns[0].amount == 25


def test_hand_outcome_rejects_unbalanced_gross_awards() -> None:
    with pytest.raises(ValueError, match="entire contestable pot"):
        HandOutcome(total_pot=100, gross_awards=(PlayerAward("player-a", 99),))


def test_poker_event_uses_typed_kind_and_card_amount_fields() -> None:
    event = PokerEvent(PokerEventKind.PLAYER_CALLED, "player-a", amount=50)

    assert event.kind is PokerEventKind.PLAYER_CALLED
    assert event.amount == 50