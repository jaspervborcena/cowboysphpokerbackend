import pytest

from game_engine import (
    HandCategory,
    HandRank,
    Pot,
    build_side_pots,
    resolve_pot_awards,
)


def test_side_pots_exclude_folded_players_and_return_uncalled_excess() -> None:
    contributions = {
        "alice": 100,
        "bob": 60,
        "carol": 20,
        "dana": 40,
    }

    result = build_side_pots(contributions, folded_player_ids={"dana"})

    assert result.pots == (
        Pot(80, ("alice", "bob", "carol")),
        Pot(100, ("alice", "bob")),
    )
    assert result.uncalled_returns == {"alice": 40}
    assert sum(pot.amount for pot in result.pots) + sum(result.uncalled_returns.values()) == sum(contributions.values())

    hand_ranks = {
        "alice": HandRank(HandCategory.ONE_PAIR, (13, 14, 9, 4)),
        "bob": HandRank(HandCategory.HIGH_CARD, (14, 12, 9, 4, 2)),
        "carol": HandRank(HandCategory.STRAIGHT, (8,)),
    }
    awards = resolve_pot_awards(result.pots, hand_ranks, ["alice", "bob", "carol", "dana"])

    assert awards == {"carol": 80, "alice": 100}
    assert sum(awards.values()) + sum(result.uncalled_returns.values()) == sum(contributions.values())


def test_tied_pot_splits_odd_chip_in_button_order() -> None:
    pot = Pot(11, ("alice", "bob", "carol"))
    tied_rank = HandRank(HandCategory.TWO_PAIR, (12, 8, 14))
    hand_ranks = {
        "alice": tied_rank,
        "bob": tied_rank,
        "carol": HandRank(HandCategory.ONE_PAIR, (14, 13, 10, 4)),
    }

    awards = resolve_pot_awards([pot], hand_ranks, ["bob", "alice", "carol"])

    assert awards == {"bob": 6, "alice": 5}


def test_all_contributors_folded_is_invalid_contested_pot() -> None:
    with pytest.raises(ValueError, match="eligible player"):
        build_side_pots({"alice": 100, "bob": 100}, {"alice", "bob"})


@pytest.mark.parametrize(
    "contributions",
    [
        {"alice": -1},
        {"alice": True},
        {"": 10},
    ],
)
def test_rejects_invalid_contributions(contributions: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_side_pots(contributions)  # type: ignore[arg-type]


def test_award_resolution_requires_ranks_for_all_eligible_players() -> None:
    pot = Pot(10, ("alice", "bob"))

    with pytest.raises(ValueError, match="hand rank"):
        resolve_pot_awards(
            [pot],
            {"alice": HandRank(HandCategory.HIGH_CARD, (14, 12, 9, 5, 2))},
            ["alice", "bob"],
        )