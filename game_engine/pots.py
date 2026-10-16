from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from game_engine.hand_evaluator import HandRank


@dataclass(frozen=True)
class Pot:
    amount: int
    eligible_player_ids: tuple[str, ...]


@dataclass(frozen=True)
class PotBuildResult:
    pots: tuple[Pot, ...]
    uncalled_returns: dict[str, int]


def build_side_pots(
    contributions: Mapping[str, int],
    folded_player_ids: set[str] | frozenset[str] = frozenset(),
) -> PotBuildResult:
    """Build contestable pots from total hand contributions.

    Unmatched excess contributions are returned to their contributor instead of
    being treated as a pot. Folded players still contribute chips but cannot win.
    """
    if any(not isinstance(player_id, str) or not player_id for player_id in contributions):
        raise ValueError("Player IDs must be non-empty strings.")
    if any(
        isinstance(amount, bool) or not isinstance(amount, int) or amount < 0
        for amount in contributions.values()
    ):
        raise ValueError("Contributions must be non-negative integer chip amounts.")
    if not folded_player_ids.issubset(contributions):
        raise ValueError("Folded players must have a contribution entry.")

    levels = sorted({amount for amount in contributions.values() if amount > 0})
    pots: list[Pot] = []
    uncalled_returns: dict[str, int] = {}
    previous_level = 0

    for level in levels:
        contributors = tuple(
            sorted(
                player_id
                for player_id, amount in contributions.items()
                if amount >= level
            )
        )
        tranche = (level - previous_level) * len(contributors)
        previous_level = level

        if len(contributors) == 1:
            player_id = contributors[0]
            uncalled_returns[player_id] = uncalled_returns.get(player_id, 0) + tranche
            continue

        eligible = tuple(
            player_id for player_id in contributors if player_id not in folded_player_ids
        )
        if not eligible:
            raise ValueError("A contestable pot must have an eligible player.")

        if pots and pots[-1].eligible_player_ids == eligible:
            previous_pot = pots[-1]
            pots[-1] = Pot(previous_pot.amount + tranche, eligible)
        else:
            pots.append(Pot(tranche, eligible))

    return PotBuildResult(tuple(pots), uncalled_returns)


def resolve_pot_awards(
    pots: Sequence[Pot],
    hand_ranks: Mapping[str, HandRank],
    odd_chip_order: Sequence[str],
) -> dict[str, int]:
    """Resolve gross pot awards; the order starts with the first seat left of button."""
    if len(set(odd_chip_order)) != len(odd_chip_order):
        raise ValueError("Odd-chip order cannot contain duplicate players.")

    required_players = {
        player_id for pot in pots for player_id in pot.eligible_player_ids
    }
    if not required_players.issubset(hand_ranks):
        raise ValueError("Every eligible player must have a hand rank.")
    if not required_players.issubset(odd_chip_order):
        raise ValueError("Odd-chip order must include every eligible player.")
    if any(not isinstance(hand_ranks[player_id], HandRank) for player_id in required_players):
        raise ValueError("Hand ranks must be HandRank values.")

    awards: dict[str, int] = {}
    for pot in pots:
        if isinstance(pot.amount, bool) or not isinstance(pot.amount, int) or pot.amount <= 0:
            raise ValueError("Pot amounts must be positive integer chip amounts.")
        if not pot.eligible_player_ids:
            raise ValueError("A pot must have at least one eligible player.")

        best_rank = max(hand_ranks[player_id] for player_id in pot.eligible_player_ids)
        winners = {
            player_id
            for player_id in pot.eligible_player_ids
            if hand_ranks[player_id] == best_rank
        }
        ordered_winners = [
            player_id for player_id in odd_chip_order if player_id in winners
        ]
        share, odd_chips = divmod(pot.amount, len(ordered_winners))
        for player_id in ordered_winners:
            awards[player_id] = awards.get(player_id, 0) + share
        for player_id in ordered_winners[:odd_chips]:
            awards[player_id] += 1

    return awards