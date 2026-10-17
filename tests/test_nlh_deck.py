from random import Random, SystemRandom

import pytest

from game_engine import Card, Deck, DeckExhausted


def test_deck_has_52_unique_cards() -> None:
    deck = Deck.for_testing(13)
    cards = deck.draw_many(52)

    assert len(cards) == 52
    assert len(set(cards)) == 52
    assert deck.cards_remaining == 0
    assert set(cards) == {
        Card(rank, suit)
        for rank in range(2, 15)
        for suit in ("c", "d", "h", "s")
    }


def test_seeded_decks_are_reproducible() -> None:
    first = Deck.for_testing(2026).draw_many(52)
    second = Deck.for_testing(2026).draw_many(52)

    assert first == second


def test_default_deck_uses_system_random_source(monkeypatch) -> None:
    created_sources: list[SystemRandom] = []

    class RecordingSystemRandom(SystemRandom):
        def __init__(self) -> None:
            super().__init__()
            created_sources.append(self)

    monkeypatch.setattr("game_engine.deck.SystemRandom", RecordingSystemRandom)

    Deck()

    assert len(created_sources) == 1


def test_custom_shuffle_source_can_be_injected() -> None:
    deck = Deck(Random(7))

    assert len(deck.remaining_cards) == 52


def test_draw_and_burn_are_tracked_separately() -> None:
    deck = Deck.for_testing(9)
    first_draw = deck.draw()
    burned = deck.burn()
    later_draws = deck.draw_many(3)

    assert deck.dealt_cards == (first_draw, *later_draws)
    assert deck.burned_cards == (burned,)
    assert len(set(deck.dealt_cards + deck.burned_cards + deck.remaining_cards)) == 52
    assert len(deck.dealt_cards) + len(deck.burned_cards) + deck.cards_remaining == 52


def test_deck_checkpoint_round_trip_preserves_card_order_and_zones() -> None:
    deck = Deck.for_testing(83)
    deck.draw_many(4)
    deck.burn()
    checkpoint = deck.to_checkpoint()

    restored = Deck.from_checkpoint(checkpoint)

    assert restored.to_checkpoint() == checkpoint
    assert restored.draw_many(5) == deck.draw_many(5)
    assert restored.burn() == deck.burn()


def test_deck_checkpoint_rejects_duplicate_or_missing_cards() -> None:
    checkpoint = Deck.for_testing(84).to_checkpoint()
    checkpoint["remaining_cards"] = checkpoint["remaining_cards"][:-1]

    with pytest.raises(ValueError, match="each of the 52 cards exactly once"):
        Deck.from_checkpoint(checkpoint)


def test_deck_checkpoint_rejects_unknown_version() -> None:
    checkpoint = Deck.for_testing(85).to_checkpoint()
    checkpoint["version"] = 2

    with pytest.raises(ValueError, match="Unsupported deck checkpoint version"):
        Deck.from_checkpoint(checkpoint)


def test_failed_bulk_draw_does_not_partially_consume_deck() -> None:
    deck = Deck.for_testing(42)
    deck.draw_many(51)
    cards_before = deck.remaining_cards

    with pytest.raises(DeckExhausted):
        deck.draw_many(2)

    assert deck.remaining_cards == cards_before
    assert deck.cards_remaining == 1


@pytest.mark.parametrize("count", [-1, True, 1.5])
def test_draw_many_rejects_invalid_count(count: object) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        Deck.for_testing(1).draw_many(count)  # type: ignore[arg-type]