from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any

from game_engine.contracts import HandOutcome, NLH_GAME_ID, PlayerSeed, PokerTableConfig
from game_engine.dealing import (
    deal_flop,
    deal_hole_cards,
    deal_river,
    deal_turn,
    nlh_deal_order,
    visible_hole_cards,
)
from game_engine.deck import Deck
from game_engine.hand_evaluator import Card


class NLHStreet(IntEnum):
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3
    SHOWDOWN = 4
    COMPLETE = 5


@dataclass(frozen=True)
class NLHPlayerState:
    player_id: str
    display_name: str
    seat_index: int
    starting_stack: int
    stack: int
    hole_cards: tuple[Card, Card]
    total_contribution: int = 0
    street_contribution: int = 0
    last_action_bet: int = 0
    folded: bool = False
    all_in: bool = False


@dataclass(frozen=True)
class NLHHandState:
    table_id: str
    hand_id: str
    config: PokerTableConfig
    players: tuple[NLHPlayerState, ...]
    button_seat: int
    deck: Deck
    street: NLHStreet = NLHStreet.PREFLOP
    community_cards: tuple[Card, ...] = ()
    publicly_revealed_player_ids: frozenset[str] = frozenset()
    current_bet: int = 0
    minimum_raise: int = 0
    actor_player_id: str | None = None
    acted_player_ids: frozenset[str] = frozenset()
    outcome: HandOutcome | None = None

    def reveal_hole_cards(self, player_ids: set[str] | frozenset[str]) -> NLHHandState:
        seated_players = {player.player_id for player in self.players}
        if not player_ids.issubset(seated_players):
            raise ValueError("Only seated players' cards can be publicly revealed.")
        return replace(
            self,
            publicly_revealed_player_ids=(
                self.publicly_revealed_player_ids | frozenset(player_ids)
            ),
        )


def advance_nlh_street(state: NLHHandState) -> NLHHandState:
    """Deal the next community-card street after betting for the current street ends."""
    if state.street is NLHStreet.PREFLOP:
        if state.community_cards:
            raise ValueError("Preflop state cannot already have community cards.")
        community_cards = deal_flop(state.deck)
        street = NLHStreet.FLOP
    elif state.street is NLHStreet.FLOP:
        if len(state.community_cards) != 3:
            raise ValueError("Flop state must have exactly three community cards.")
        community_cards = (*state.community_cards, deal_turn(state.deck))
        street = NLHStreet.TURN
    elif state.street is NLHStreet.TURN:
        if len(state.community_cards) != 4:
            raise ValueError("Turn state must have exactly four community cards.")
        community_cards = (*state.community_cards, deal_river(state.deck))
        street = NLHStreet.RIVER
    else:
        raise ValueError("Only preflop, flop, and turn states can deal a street.")
    return replace(state, street=street, community_cards=community_cards)


def begin_nlh_showdown(state: NLHHandState) -> NLHHandState:
    """Enter showdown on a complete board and reveal every non-folded player's cards."""
    if state.street is not NLHStreet.RIVER or len(state.community_cards) != 5:
        raise ValueError("Showdown requires a fully dealt river board.")
    return replace(
        state,
        street=NLHStreet.SHOWDOWN,
        publicly_revealed_player_ids=frozenset(
            player.player_id for player in state.players if not player.folded
        ),
    )


def initialize_nlh_hand(
    table_id: str,
    hand_id: str,
    config: PokerTableConfig,
    players: tuple[PlayerSeed, ...],
    button_seat: int,
    deck: Deck,
    *,
    allow_unoccupied_button: bool = False,
) -> NLHHandState:
    """Create the dealt preflop state without posting blinds or selecting an actor."""
    if not table_id or not hand_id:
        raise ValueError("Table ID and hand ID must be non-empty.")
    if config.game_id != NLH_GAME_ID:
        raise ValueError("NLH state requires the no-limit Texas Hold'em game ID.")
    if not 2 <= len(players) <= config.max_seats:
        raise ValueError("A hand requires at least two players within table capacity.")
    if any(player.starting_stack <= 0 for player in players):
        raise ValueError("Every player must have chips to start a hand.")
    if len({player.player_id for player in players}) != len(players):
        raise ValueError("Player IDs must be unique in a hand.")
    if len({player.seat_index for player in players}) != len(players):
        raise ValueError("Seat indices must be unique in a hand.")
    if any(player.seat_index >= config.max_seats for player in players):
        raise ValueError("Player seat is outside the configured table capacity.")

    players_by_seat = {player.seat_index: player for player in players}
    deal_order = nlh_deal_order(
        {seat: player.player_id for seat, player in players_by_seat.items()},
        button_seat,
        config.max_seats,
        allow_unoccupied_button=allow_unoccupied_button,
    )
    hole_cards = deal_hole_cards(deck, deal_order)
    state_players = tuple(
        NLHPlayerState(
            player_id=player.player_id,
            display_name=player.display_name or player.player_id,
            seat_index=player.seat_index,
            starting_stack=player.starting_stack,
            stack=player.starting_stack,
            hole_cards=hole_cards[player.player_id],
        )
        for player in sorted(players, key=lambda item: item.seat_index)
    )
    return NLHHandState(
        table_id=table_id,
        hand_id=hand_id,
        config=config,
        players=state_players,
        button_seat=button_seat,
        deck=deck,
    )


def project_player_hand_state(
    state: NLHHandState,
    viewer_player_id: str,
) -> dict[str, Any]:
    """Project only public state and cards authorized for this viewer."""
    seated_player_ids = {player.player_id for player in state.players}
    if viewer_player_id not in seated_player_ids:
        raise ValueError("Viewer must be seated at this table.")

    return {
        "table_id": state.table_id,
        "game_id": state.config.game_id,
        "hand_id": state.hand_id,
        "is_hand_running": state.street is not NLHStreet.COMPLETE,
        "street": int(state.street),
        "community_cards": [card.code for card in state.community_cards],
        "active_turn_seat": next(
            (
                player.seat_index
                for player in state.players
                if player.player_id == state.actor_player_id
            ),
            None,
        ),
        "players": [
            {
                "seat": player.seat_index,
                "user_id": player.player_id,
                "username": player.display_name,
                "stack": player.stack,
                "status": "sitting_in",
                "hole_cards": list(
                    visible_hole_cards(
                        player.player_id,
                        player.hole_cards,
                        viewer_player_id,
                        publicly_revealed=(
                            player.player_id in state.publicly_revealed_player_ids
                        ),
                    )
                ),
                "in_hand": not player.folded,
            }
            for player in state.players
        ],
    }