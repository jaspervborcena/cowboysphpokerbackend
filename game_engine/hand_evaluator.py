from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
from itertools import combinations
from typing import Iterable


_RANKS = {
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
    "8": 8,
    "9": 9,
    "T": 10,
    "J": 11,
    "Q": 12,
    "K": 13,
    "A": 14,
}
_RANK_CODES = {rank: code for code, rank in _RANKS.items()}
_SUITS = frozenset({"c", "d", "h", "s"})


@dataclass(frozen=True, order=True)
class Card:
    rank: int
    suit: str

    def __post_init__(self) -> None:
        if self.rank not in _RANK_CODES:
            raise ValueError("Card rank must be between 2 and 14.")
        if self.suit not in _SUITS:
            raise ValueError("Card suit must be one of clubs, diamonds, hearts, or spades.")

    @classmethod
    def from_code(cls, code: str) -> Card:
        if not isinstance(code, str):
            raise ValueError("Card code must be a string.")
        normalized = code.strip()
        if len(normalized) == 3 and normalized[:2] == "10":
            rank_code, suit = "T", normalized[2].lower()
        elif len(normalized) == 2:
            rank_code, suit = normalized[0].upper(), normalized[1].lower()
        else:
            raise ValueError(f"Invalid card code: {code!r}.")
        rank = _RANKS.get(rank_code)
        if rank is None or suit not in _SUITS:
            raise ValueError(f"Invalid card code: {code!r}.")
        return cls(rank, suit)

    @property
    def code(self) -> str:
        return f"{_RANK_CODES[self.rank]}{self.suit}"


class HandCategory(IntEnum):
    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


@dataclass(frozen=True, order=True)
class HandRank:
    category: HandCategory
    tiebreakers: tuple[int, ...]


def evaluate_hand(cards: Iterable[str | Card]) -> HandRank:
    """Rank a five-to-seven-card Texas Hold'em hand, best five cards included."""
    parsed = tuple(card if isinstance(card, Card) else Card.from_code(card) for card in cards)
    if not 5 <= len(parsed) <= 7:
        raise ValueError("A Texas Hold'em hand must contain five to seven cards.")
    if len(set(parsed)) != len(parsed):
        raise ValueError("A hand cannot contain duplicate cards.")
    return max(_rank_five_cards(five_cards) for five_cards in combinations(parsed, 5))


def _rank_five_cards(cards: tuple[Card, ...]) -> HandRank:
    ranks = sorted((card.rank for card in cards), reverse=True)
    rank_counts = Counter(ranks)
    groups = sorted(
        ((count, rank) for rank, count in rank_counts.items()), reverse=True
    )
    is_flush = len({card.suit for card in cards}) == 1
    straight_high = _straight_high(ranks)

    if is_flush and straight_high is not None:
        return HandRank(HandCategory.STRAIGHT_FLUSH, (straight_high,))
    if groups[0][0] == 4:
        quad_rank = groups[0][1]
        kicker = groups[1][1]
        return HandRank(HandCategory.FOUR_OF_A_KIND, (quad_rank, kicker))
    if groups[0][0] == 3 and groups[1][0] == 2:
        return HandRank(HandCategory.FULL_HOUSE, (groups[0][1], groups[1][1]))
    if is_flush:
        return HandRank(HandCategory.FLUSH, tuple(ranks))
    if straight_high is not None:
        return HandRank(HandCategory.STRAIGHT, (straight_high,))
    if groups[0][0] == 3:
        trip_rank = groups[0][1]
        kickers = tuple(rank for rank in ranks if rank != trip_rank)
        return HandRank(HandCategory.THREE_OF_A_KIND, (trip_rank, *kickers))
    if groups[0][0] == 2 and groups[1][0] == 2:
        high_pair, low_pair = sorted((groups[0][1], groups[1][1]), reverse=True)
        kicker = next(rank for rank in ranks if rank != high_pair and rank != low_pair)
        return HandRank(HandCategory.TWO_PAIR, (high_pair, low_pair, kicker))
    if groups[0][0] == 2:
        pair_rank = groups[0][1]
        kickers = tuple(rank for rank in ranks if rank != pair_rank)
        return HandRank(HandCategory.ONE_PAIR, (pair_rank, *kickers))
    return HandRank(HandCategory.HIGH_CARD, tuple(ranks))


def _straight_high(ranks: list[int]) -> int | None:
    unique_ranks = sorted(set(ranks), reverse=True)
    if len(unique_ranks) != 5:
        return None
    if unique_ranks == [14, 5, 4, 3, 2]:
        return 5
    if unique_ranks[0] - unique_ranks[-1] == 4:
        return unique_ranks[0]
    return None