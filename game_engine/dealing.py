from __future__ import annotations

from collections.abc import Sequence

from game_engine.deck import Deck, DeckExhausted
from game_engine.hand_evaluator import Card


def deal_hole_cards(
    deck: Deck,
    deal_order: Sequence[str],
) -> dict[str, tuple[Card, Card]]:
    """Deal two rounds in caller-supplied order (first seat left of the button)."""
    if len(deal_order) < 2:
        raise ValueError("A poker hand requires at least two players.")
    if any(not player_id for player_id in deal_order):
        raise ValueError("Player IDs must be non-empty.")
    if len(set(deal_order)) != len(deal_order):
        raise ValueError("Deal order cannot contain duplicate players.")
    if deck.cards_remaining < 2 * len(deal_order):
        raise DeckExhausted("The deck does not contain enough cards to deal hole cards.")

    dealt: dict[str, list[Card]] = {player_id: [] for player_id in deal_order}
    for _ in range(2):
        for player_id in deal_order:
            dealt[player_id].append(deck.draw())
    return {
        player_id: (cards[0], cards[1])
        for player_id, cards in dealt.items()
    }


def nlh_deal_order(
    player_ids_by_seat: dict[int, str],
    button_seat: int,
    max_seats: int,
    *,
    allow_unoccupied_button: bool = False,
) -> tuple[str, ...]:
    """Return the clockwise hole-card order, handling the heads-up button rule."""
    if max_seats < 2:
        raise ValueError("A poker table must support at least two seats.")
    if len(player_ids_by_seat) < 2:
        raise ValueError("A poker hand requires at least two players.")
    if not 0 <= button_seat < max_seats:
        raise ValueError("Button seat is outside the configured table capacity.")
    if not allow_unoccupied_button and button_seat not in player_ids_by_seat:
        raise ValueError("Button must be assigned to a seated player.")
    if any(not 0 <= seat < max_seats for seat in player_ids_by_seat):
        raise ValueError("Player seat is outside the configured table capacity.")
    if len(set(player_ids_by_seat.values())) != len(player_ids_by_seat):
        raise ValueError("Player IDs must be unique.")

    if len(player_ids_by_seat) == 2 and button_seat in player_ids_by_seat:
        first_seat = button_seat
    else:
        first_seat = next(
            (button_seat + offset) % max_seats
            for offset in range(1, max_seats + 1)
            if (button_seat + offset) % max_seats in player_ids_by_seat
        )

    ordered_seats = [
        (first_seat + offset) % max_seats
        for offset in range(max_seats)
        if (first_seat + offset) % max_seats in player_ids_by_seat
    ]
    return tuple(player_ids_by_seat[seat] for seat in ordered_seats)


def deal_flop(deck: Deck) -> tuple[Card, Card, Card]:
    if deck.cards_remaining < 4:
        raise DeckExhausted("The deck does not contain enough cards to deal the flop.")
    deck.burn()
    cards = deck.draw_many(3)
    return cards[0], cards[1], cards[2]


def deal_turn(deck: Deck) -> Card:
    return _deal_one_board_card(deck, "turn")


def deal_river(deck: Deck) -> Card:
    return _deal_one_board_card(deck, "river")


def visible_hole_cards(
    owner_player_id: str,
    cards: Sequence[Card],
    viewer_player_id: str,
    *,
    publicly_revealed: bool = False,
) -> tuple[str, ...]:
    """Return clear cards to their owner/revealed viewers and masks to opponents."""
    if viewer_player_id == owner_player_id or publicly_revealed:
        return tuple(card.code for card in cards)
    return tuple("??" for _ in cards)


def _deal_one_board_card(deck: Deck, street: str) -> Card:
    if deck.cards_remaining < 2:
        raise DeckExhausted(f"The deck does not contain enough cards to deal the {street}.")
    deck.burn()
    return deck.draw()