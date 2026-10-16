import pytest

from game_engine import (
    Deck,
    DeckExhausted,
    deal_flop,
    deal_hole_cards,
    deal_river,
    deal_turn,
    visible_hole_cards,
)


def test_hole_cards_are_dealt_in_two_rounds_in_supplied_order() -> None:
    deck = Deck.for_testing(21)

    hole_cards = deal_hole_cards(deck, ["alice", "bob", "carol"])

    dealt = deck.dealt_cards
    assert hole_cards == {
        "alice": (dealt[0], dealt[3]),
        "bob": (dealt[1], dealt[4]),
        "carol": (dealt[2], dealt[5]),
    }
    assert deck.cards_remaining == 46


def test_community_dealing_burns_before_each_street() -> None:
    deck = Deck.for_testing(22)
    hole_cards = deal_hole_cards(deck, ["alice", "bob"])

    flop = deal_flop(deck)
    turn = deal_turn(deck)
    river = deal_river(deck)
    board = (*flop, turn, river)

    assert len(board) == 5
    assert len(set((*board, *hole_cards["alice"], *hole_cards["bob"], *deck.burned_cards))) == 12
    assert len(deck.burned_cards) == 3
    assert len(deck.dealt_cards) == 9
    assert deck.cards_remaining == 40


def test_opponents_cards_are_masked_unless_publicly_revealed() -> None:
    deck = Deck.for_testing(23)
    alice_cards, bob_cards = deal_hole_cards(deck, ["alice", "bob"]).values()

    assert visible_hole_cards("alice", alice_cards, "alice") == tuple(
        card.code for card in alice_cards
    )
    assert visible_hole_cards("alice", alice_cards, "bob") == ("??", "??")
    assert visible_hole_cards(
        "alice", alice_cards, "bob", publicly_revealed=True
    ) == tuple(card.code for card in alice_cards)
    assert visible_hole_cards("alice", (), "bob") == ()


@pytest.mark.parametrize("deal_order", [[], ["alice"], ["alice", "alice"]])
def test_hole_card_deal_rejects_invalid_order(deal_order: list[str]) -> None:
    with pytest.raises(ValueError):
        deal_hole_cards(Deck.for_testing(24), deal_order)


def test_hole_card_deal_checks_capacity_before_consuming_cards() -> None:
    deck = Deck.for_testing(25)
    deck.draw_many(49)
    cards_before = deck.remaining_cards

    with pytest.raises(DeckExhausted):
        deal_hole_cards(deck, ["alice", "bob"])

    assert deck.remaining_cards == cards_before


def test_board_deal_fails_cleanly_when_not_enough_cards_remain() -> None:
    deck = Deck.for_testing(26)
    deck.draw_many(49)
    cards_before = deck.remaining_cards

    with pytest.raises(DeckExhausted):
        deal_flop(deck)

    assert deck.remaining_cards == cards_before

    deck.draw_many(2)
    for deal_next in (deal_turn, deal_river):
        cards_before = deck.remaining_cards
        with pytest.raises(DeckExhausted):
            deal_next(deck)
        assert deck.remaining_cards == cards_before