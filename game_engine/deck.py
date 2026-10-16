from __future__ import annotations

from collections.abc import MutableSequence
from random import Random, SystemRandom
from typing import Protocol

from game_engine.hand_evaluator import Card


class ShuffleSource(Protocol):
    def shuffle(self, values: MutableSequence[Card]) -> None: ...


class DeckExhausted(ValueError):
    pass


class Deck:
    """A shuffled 52-card deck with injectable randomness for reproducible tests."""

    def __init__(self, shuffle_source: ShuffleSource | None = None) -> None:
        self._remaining = [
            Card(rank, suit)
            for suit in ("c", "d", "h", "s")
            for rank in range(2, 15)
        ]
        source = shuffle_source if shuffle_source is not None else SystemRandom()
        source.shuffle(self._remaining)
        self._dealt: list[Card] = []
        self._burned: list[Card] = []

    @classmethod
    def for_testing(cls, seed: int) -> Deck:
        """Create a reproducible deck; never use this factory for live games."""
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("Test deck seed must be an integer.")
        return cls(Random(seed))

    @property
    def remaining_cards(self) -> tuple[Card, ...]:
        return tuple(self._remaining)

    @property
    def dealt_cards(self) -> tuple[Card, ...]:
        return tuple(self._dealt)

    @property
    def burned_cards(self) -> tuple[Card, ...]:
        return tuple(self._burned)

    @property
    def cards_remaining(self) -> int:
        return len(self._remaining)

    def draw(self) -> Card:
        card = self._take_next_card()
        self._dealt.append(card)
        return card

    def draw_many(self, count: int) -> tuple[Card, ...]:
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Draw count must be a non-negative integer.")
        if count > len(self._remaining):
            raise DeckExhausted("The deck does not contain enough cards.")
        return tuple(self.draw() for _ in range(count))

    def burn(self) -> Card:
        card = self._take_next_card()
        self._burned.append(card)
        return card

    def _take_next_card(self) -> Card:
        if not self._remaining:
            raise DeckExhausted("The deck is empty.")
        return self._remaining.pop()