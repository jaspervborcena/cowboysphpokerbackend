import json
from pathlib import Path
from typing import Any

import pytest

from game_engine import (
	Card,
	HandCategory,
	HandRank,
	Pot,
	build_side_pots,
	evaluate_hand,
	resolve_pot_awards,
)


FIXTURES_PATH = Path(__file__).parent / "fixtures" / "nlh_example_hands.json"
with FIXTURES_PATH.open(encoding="utf-8") as fixtures_file:
	HAND_FIXTURES = json.load(fixtures_file)


@pytest.mark.parametrize("fixture", HAND_FIXTURES, ids=lambda fixture: fixture["name"])
def test_nlh_example_hand_outcomes(fixture: dict[str, Any]) -> None:
	board = fixture["board"]
	players = fixture["players"]
	player_ids = [player["player_id"] for player in players]
	all_cards = [*board, *(card for player in players for card in player["hole_cards"])]

	assert len({Card.from_code(card) for card in all_cards}) == len(all_cards)

	hand_ranks = {
		player["player_id"]: evaluate_hand([*player["hole_cards"], *board])
		for player in players
	}
	expected_ranks = {
		player_id: HandRank(
			HandCategory[expected_rank["category"]],
			tuple(expected_rank["tiebreakers"]),
		)
		for player_id, expected_rank in fixture["expected_ranks"].items()
	}
	assert hand_ranks == expected_ranks

	pot_result = build_side_pots(
		{player["player_id"]: player["contribution"] for player in players},
		set(fixture["folded_player_ids"]),
	)
	expected_pots = tuple(
		Pot(pot["amount"], tuple(pot["eligible_player_ids"]))
		for pot in fixture["expected_pots"]
	)
	assert pot_result.pots == expected_pots
	assert pot_result.uncalled_returns == fixture["expected_uncalled_returns"]

	awards = resolve_pot_awards(
		pot_result.pots,
		hand_ranks,
		fixture["odd_chip_order"],
	)
	assert awards == fixture["expected_awards"]
	assert (
		sum(awards.values()) + sum(pot_result.uncalled_returns.values())
		== sum(player["contribution"] for player in players)
	)