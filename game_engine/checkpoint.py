from __future__ import annotations

from typing import Any

from game_engine.contracts import (
    HandOutcome,
    NLH_GAME_ID,
    PlayerAward,
    PokerGameMode,
    PokerTableConfig,
)
from game_engine.deck import Deck
from game_engine.hand_evaluator import Card
from game_engine.nlh_state import NLHHandState, NLHPlayerState, NLHStreet


NLH_CHECKPOINT_VERSION = 1


def serialize_nlh_hand_state(state: NLHHandState) -> dict[str, Any]:
    return {
        "schema_version": NLH_CHECKPOINT_VERSION,
        "table_id": state.table_id,
        "hand_id": state.hand_id,
        "config": {
            "game_id": state.config.game_id,
            "small_blind": state.config.small_blind,
            "big_blind": state.config.big_blind,
            "max_seats": state.config.max_seats,
            "game_mode": state.config.game_mode.value,
        },
        "players": [
            {
                "player_id": player.player_id,
                "display_name": player.display_name,
                "seat_index": player.seat_index,
                "starting_stack": player.starting_stack,
                "stack": player.stack,
                "hole_cards": [card.code for card in player.hole_cards],
                "total_contribution": player.total_contribution,
                "street_contribution": player.street_contribution,
                "last_action_bet": player.last_action_bet,
                "folded": player.folded,
                "all_in": player.all_in,
            }
            for player in state.players
        ],
        "button_seat": state.button_seat,
        "deck": state.deck.to_checkpoint(),
        "street": int(state.street),
        "community_cards": [card.code for card in state.community_cards],
        "publicly_revealed_player_ids": sorted(state.publicly_revealed_player_ids),
        "current_bet": state.current_bet,
        "minimum_raise": state.minimum_raise,
        "actor_player_id": state.actor_player_id,
        "acted_player_ids": sorted(state.acted_player_ids),
        "outcome": _serialize_outcome(state.outcome),
    }


def deserialize_nlh_hand_state(checkpoint: object) -> NLHHandState:
    root = _object(checkpoint, "checkpoint")
    version = _integer(root.get("schema_version"), "schema_version", minimum=1)
    if version != NLH_CHECKPOINT_VERSION:
        raise ValueError("Unsupported NLH checkpoint version.")

    table_id = _string(root.get("table_id"), "table_id")
    hand_id = _string(root.get("hand_id"), "hand_id")
    config_data = _object(root.get("config"), "config")
    game_id = _string(config_data.get("game_id"), "config.game_id")
    if game_id != NLH_GAME_ID:
        raise ValueError("Checkpoint game_id is not supported by the NLH engine.")
    try:
        game_mode = PokerGameMode(_string(config_data.get("game_mode"), "config.game_mode"))
        config = PokerTableConfig(
            game_id=game_id,
            small_blind=_integer(config_data.get("small_blind"), "config.small_blind", minimum=1),
            big_blind=_integer(config_data.get("big_blind"), "config.big_blind", minimum=1),
            max_seats=_integer(config_data.get("max_seats"), "config.max_seats", minimum=2),
            game_mode=game_mode,
        )
    except ValueError as error:
        raise ValueError(f"Invalid checkpoint table config: {error}") from error

    player_data = _list(root.get("players"), "players")
    if not 2 <= len(player_data) <= config.max_seats:
        raise ValueError("Checkpoint must contain two or more players within table capacity.")
    players = tuple(_deserialize_player(item, index) for index, item in enumerate(player_data))
    player_ids = {player.player_id for player in players}
    player_seats = {player.seat_index for player in players}
    if len(player_ids) != len(players) or len(player_seats) != len(players):
        raise ValueError("Checkpoint player IDs and seats must be unique.")
    if any(player.seat_index >= config.max_seats for player in players):
        raise ValueError("Checkpoint player seat exceeds table capacity.")

    button_seat = _integer(root.get("button_seat"), "button_seat", minimum=0)
    if button_seat >= config.max_seats:
        raise ValueError("Checkpoint button seat exceeds table capacity.")
    deck = Deck.from_checkpoint(root.get("deck"))
    try:
        street = NLHStreet(_integer(root.get("street"), "street", minimum=0))
    except ValueError as error:
        raise ValueError("Checkpoint street is invalid.") from error
    community_cards = _cards(root.get("community_cards"), "community_cards")
    if len(community_cards) > 5:
        raise ValueError("Checkpoint cannot contain more than five community cards.")
    dealt_from_state = [card for player in players for card in player.hole_cards]
    dealt_from_state.extend(community_cards)
    if len(set(dealt_from_state)) != len(dealt_from_state):
        raise ValueError("Checkpoint contains duplicate hole or community cards.")
    if set(dealt_from_state) != set(deck.dealt_cards):
        raise ValueError("Checkpoint hole and community cards do not match the deck's dealt cards.")

    publicly_revealed = _string_set(
        root.get("publicly_revealed_player_ids"),
        "publicly_revealed_player_ids",
    )
    acted_player_ids = _string_set(root.get("acted_player_ids"), "acted_player_ids")
    if not publicly_revealed.issubset(player_ids) or not acted_player_ids.issubset(player_ids):
        raise ValueError("Checkpoint player-state references must refer to seated players.")

    actor = root.get("actor_player_id")
    if actor is not None and (not isinstance(actor, str) or actor not in player_ids):
        raise ValueError("Checkpoint actor must be null or a seated player ID.")

    return NLHHandState(
        table_id=table_id,
        hand_id=hand_id,
        config=config,
        players=players,
        button_seat=button_seat,
        deck=deck,
        street=street,
        community_cards=community_cards,
        publicly_revealed_player_ids=publicly_revealed,
        current_bet=_integer(root.get("current_bet"), "current_bet", minimum=0),
        minimum_raise=_integer(root.get("minimum_raise"), "minimum_raise", minimum=0),
        actor_player_id=actor,
        acted_player_ids=acted_player_ids,
        outcome=_deserialize_outcome(root.get("outcome")),
    )


def _serialize_outcome(outcome: HandOutcome | None) -> dict[str, Any] | None:
    if outcome is None:
        return None
    return {
        "total_pot": outcome.total_pot,
        "gross_awards": [
            {"player_id": award.player_id, "amount": award.amount}
            for award in outcome.gross_awards
        ],
        "uncalled_returns": [
            {"player_id": award.player_id, "amount": award.amount}
            for award in outcome.uncalled_returns
        ],
    }


def _deserialize_outcome(value: object) -> HandOutcome | None:
    if value is None:
        return None
    outcome = _object(value, "outcome")
    return HandOutcome(
        total_pot=_integer(outcome.get("total_pot"), "outcome.total_pot", minimum=0),
        gross_awards=_awards(outcome.get("gross_awards"), "outcome.gross_awards"),
        uncalled_returns=_awards(
            outcome.get("uncalled_returns"),
            "outcome.uncalled_returns",
        ),
    )


def _deserialize_player(value: object, index: int) -> NLHPlayerState:
    label = f"players[{index}]"
    player = _object(value, label)
    hole_cards = _cards(player.get("hole_cards"), f"{label}.hole_cards")
    if len(hole_cards) != 2:
        raise ValueError(f"{label}.hole_cards must contain exactly two cards.")
    return NLHPlayerState(
        player_id=_string(player.get("player_id"), f"{label}.player_id"),
        display_name=_string(player.get("display_name"), f"{label}.display_name"),
        seat_index=_integer(player.get("seat_index"), f"{label}.seat_index", minimum=0),
        starting_stack=_integer(player.get("starting_stack"), f"{label}.starting_stack", minimum=1),
        stack=_integer(player.get("stack"), f"{label}.stack", minimum=0),
        hole_cards=(hole_cards[0], hole_cards[1]),
        total_contribution=_integer(player.get("total_contribution"), f"{label}.total_contribution", minimum=0),
        street_contribution=_integer(player.get("street_contribution"), f"{label}.street_contribution", minimum=0),
        last_action_bet=_integer(player.get("last_action_bet"), f"{label}.last_action_bet", minimum=0),
        folded=_boolean(player.get("folded"), f"{label}.folded"),
        all_in=_boolean(player.get("all_in"), f"{label}.all_in"),
    )


def _awards(value: object, label: str) -> tuple[PlayerAward, ...]:
    awards = _list(value, label)
    return tuple(
        PlayerAward(
            player_id=_string(_object(item, f"{label}[{index}]").get("player_id"), f"{label}[{index}].player_id"),
            amount=_integer(_object(item, f"{label}[{index}]").get("amount"), f"{label}[{index}].amount", minimum=1),
        )
        for index, item in enumerate(awards)
    )


def _cards(value: object, label: str) -> tuple[Card, ...]:
    codes = _list(value, label)
    if any(not isinstance(code, str) for code in codes):
        raise ValueError(f"{label} must contain card-code strings.")
    return tuple(Card.from_code(code) for code in codes)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list.")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer greater than or equal to {minimum}.")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean.")
    return value


def _string_set(value: object, label: str) -> frozenset[str]:
    entries = _list(value, label)
    if any(not isinstance(item, str) or not item for item in entries):
        raise ValueError(f"{label} must contain non-empty strings.")
    if len(set(entries)) != len(entries):
        raise ValueError(f"{label} cannot contain duplicates.")
    return frozenset(entries)