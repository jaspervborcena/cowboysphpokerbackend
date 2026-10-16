from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, Sequence, TypeVar

from game_engine.deck import ShuffleSource
from game_engine.hand_evaluator import Card


StateT = TypeVar("StateT")
NLH_GAME_ID = "no_limit_texas_holdem"


class PokerGameMode(str, Enum):
    RING = "RING"
    TOURNAMENT = "TOURNAMENT"


class PokerActionKind(str, Enum):
    FOLD = "FOLD"
    CHECK = "CHECK"
    CALL = "CALL"
    RAISE = "RAISE"
    ALL_IN = "ALL_IN"


class PokerEventKind(str, Enum):
    PLAYER_FOLDED = "PLAYER_FOLDED"
    PLAYER_CHECKED = "PLAYER_CHECKED"
    PLAYER_CALLED = "PLAYER_CALLED"
    PLAYER_RAISED = "PLAYER_RAISED"
    PLAYER_WENT_ALL_IN = "PLAYER_WENT_ALL_IN"
    SMALL_BLIND_POSTED = "SMALL_BLIND_POSTED"
    BIG_BLIND_POSTED = "BIG_BLIND_POSTED"
    STRADDLE_POSTED = "STRADDLE_POSTED"
    NEW_PLAYER_POSTED_BIG_BLIND = "NEW_PLAYER_POSTED_BIG_BLIND"
    NEW_PLAYER_WAITING_FOR_BIG_BLIND = "NEW_PLAYER_WAITING_FOR_BIG_BLIND"
    CARDS_DEALT = "CARDS_DEALT"
    BOARD_DEALT = "BOARD_DEALT"
    STREET_CHANGED = "STREET_CHANGED"
    HAND_COMPLETED = "HAND_COMPLETED"


@dataclass(frozen=True)
class PokerTableConfig:
    game_id: str
    small_blind: int
    big_blind: int
    max_seats: int
    game_mode: PokerGameMode = PokerGameMode.RING

    def __post_init__(self) -> None:
        if not self.game_id:
            raise ValueError("Game ID must be non-empty.")
        if any(
            isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0
            for amount in (self.small_blind, self.big_blind)
        ):
            raise ValueError("Blinds must be positive integer chip amounts.")
        if self.big_blind < self.small_blind:
            raise ValueError("Big blind cannot be smaller than the small blind.")
        if not isinstance(self.game_mode, PokerGameMode):
            raise ValueError("Game mode must be a PokerGameMode.")
        if isinstance(self.max_seats, bool) or not isinstance(self.max_seats, int) or self.max_seats < 2:
            raise ValueError("A poker table must support at least two seats.")


@dataclass(frozen=True)
class PlayerSeed:
    player_id: str
    seat_index: int
    starting_stack: int
    display_name: str | None = None
    new_to_table: bool = False
    accept_big_blind_post: bool = False

    def __post_init__(self) -> None:
        if not self.player_id:
            raise ValueError("Player ID must be non-empty.")
        if isinstance(self.seat_index, bool) or not isinstance(self.seat_index, int) or self.seat_index < 0:
            raise ValueError("Seat index must be a non-negative integer.")
        if isinstance(self.starting_stack, bool) or not isinstance(self.starting_stack, int) or self.starting_stack < 0:
            raise ValueError("Starting stack must be a non-negative integer.")
        if self.display_name is not None and not self.display_name:
            raise ValueError("Display name must be non-empty when supplied.")
        if not isinstance(self.new_to_table, bool) or not isinstance(
            self.accept_big_blind_post, bool
        ):
            raise ValueError("New-player entry flags must be booleans.")
        if self.accept_big_blind_post and not self.new_to_table:
            raise ValueError("Only a new player can accept an entry post.")


@dataclass(frozen=True)
class PokerAction:
    player_id: str
    kind: PokerActionKind
    raise_to: int | None = None

    def __post_init__(self) -> None:
        if not self.player_id:
            raise ValueError("Action player ID must be non-empty.")
        if not isinstance(self.kind, PokerActionKind):
            raise ValueError("Action kind must be a PokerActionKind.")
        if self.kind is PokerActionKind.RAISE:
            if isinstance(self.raise_to, bool) or not isinstance(self.raise_to, int) or self.raise_to <= 0:
                raise ValueError("Raise amount must be a positive raise-to total.")
        elif self.raise_to is not None:
            raise ValueError("Only a raise action can include a raise-to amount.")


@dataclass(frozen=True)
class PokerEvent:
    kind: PokerEventKind
    player_id: str | None = None
    amount: int | None = None
    cards: tuple[Card, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, PokerEventKind):
            raise ValueError("Event kind must be a PokerEventKind.")
        if self.amount is not None and (
            isinstance(self.amount, bool)
            or not isinstance(self.amount, int)
            or self.amount < 0
        ):
            raise ValueError("Event amount must be a non-negative integer.")


@dataclass(frozen=True)
class PlayerAward:
    player_id: str
    amount: int

    def __post_init__(self) -> None:
        if not self.player_id:
            raise ValueError("Award player ID must be non-empty.")
        if isinstance(self.amount, bool) or not isinstance(self.amount, int) or self.amount <= 0:
            raise ValueError("Award amount must be a positive integer.")


@dataclass(frozen=True)
class HandOutcome:
    total_pot: int
    gross_awards: tuple[PlayerAward, ...]
    uncalled_returns: tuple[PlayerAward, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.total_pot, bool) or not isinstance(self.total_pot, int) or self.total_pot < 0:
            raise ValueError("Total pot must be a non-negative integer.")
        if len({award.player_id for award in self.gross_awards}) != len(self.gross_awards):
            raise ValueError("Gross awards must be aggregated per player.")
        if len({award.player_id for award in self.uncalled_returns}) != len(self.uncalled_returns):
            raise ValueError("Uncalled returns must be aggregated per player.")
        if sum(award.amount for award in self.gross_awards) != self.total_pot:
            raise ValueError("Gross awards must account for the entire contestable pot.")


@dataclass(frozen=True)
class EngineTransition(Generic[StateT]):
    state: StateT
    events: tuple[PokerEvent, ...]
    outcome: HandOutcome | None = None


class PokerEngine(Protocol[StateT]):
    game_id: str

    def create_state(
        self,
        config: PokerTableConfig,
        players: Sequence[PlayerSeed],
        *,
        table_id: str,
        hand_id: str,
        button_seat: int,
        straddle_player_id: str | None = None,
        shuffle_source: ShuffleSource,
    ) -> StateT: ...

    def legal_actions(self, state: StateT, player_id: str) -> tuple[PokerAction, ...]: ...

    def apply_action(
        self, state: StateT, action: PokerAction
    ) -> EngineTransition[StateT]: ...