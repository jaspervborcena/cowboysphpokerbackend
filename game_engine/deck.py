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

    CHECKPOINT_VERSION = 1

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

    def to_checkpoint(self) -> dict[str, object]:
        return {
            "version": self.CHECKPOINT_VERSION,
            "remaining_cards": [card.code for card in self._remaining],
            "dealt_cards": [card.code for card in self._dealt],
            "burned_cards": [card.code for card in self._burned],
        }

    @classmethod
    def from_checkpoint(cls, checkpoint: object) -> Deck:
        if not isinstance(checkpoint, dict):
            raise ValueError("Deck checkpoint must be an object.")
        if checkpoint.get("version") != cls.CHECKPOINT_VERSION:
            raise ValueError("Unsupported deck checkpoint version.")

        def read_cards(field: str) -> list[Card]:
            value = checkpoint.get(field)
            if not isinstance(value, list) or any(not isinstance(code, str) for code in value):
                raise ValueError(f"Deck checkpoint {field} must be a list of card codes.")
            return [Card.from_code(code) for code in value]

        remaining = read_cards("remaining_cards")
        dealt = read_cards("dealt_cards")
        burned = read_cards("burned_cards")
        all_cards = (*remaining, *dealt, *burned)
        expected_cards = {
            Card(rank, suit)
            for suit in ("c", "d", "h", "s")
            for rank in range(2, 15)
        }
        if len(all_cards) != 52 or set(all_cards) != expected_cards:
            raise ValueError("Deck checkpoint must contain each of the 52 cards exactly once.")

        deck = cls.__new__(cls)
        deck._remaining = remaining
        deck._dealt = dealt
        deck._burned = burned
        return deck

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