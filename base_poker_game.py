from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pokerkit import Automation, NoLimitTexasHoldem


@dataclass
class PlayerSeat:
    user_id: str
    username: str
    stack: int
    buy_in_id: str | None = None
    buy_in_amount: int = 0
    status: str = "sitting_in"
    club_id: str | None = None


class BasePokerGame:
    def __init__(
        self,
        table_id: str,
        small_blind: int,
        big_blind: int,
        buy_in_min: int,
        buy_in_max: int,
        max_seats: int = 9,
        *,
        union_id: str | None = None,
        origin_club_id: str | None = None,
        table_name: str | None = None,
    ) -> None:
        if small_blind <= 0 or big_blind < small_blind:
            raise ValueError("Blinds must be positive and the big blind must be at least the small blind.")
        if buy_in_min <= 0 or buy_in_max < buy_in_min:
            raise ValueError("Buy-in limits are invalid.")
        if max_seats < 2:
            raise ValueError("A table must allow at least two seats.")

        self.table_id = table_id
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.buy_in_min = buy_in_min
        self.buy_in_max = buy_in_max
        self.max_seats = max_seats
        self.union_id = union_id
        self.origin_club_id = origin_club_id
        self.table_name = table_name or table_id
        self.seats: dict[int, PlayerSeat] = {}
        self.poker_game: Any | None = None
        self.hand_history: list[dict[str, Any]] = []
        self.is_hand_running = False
        self._player_order: list[int] = []
        self._button_offset = 0
        self._current_hand_id: str | None = None
        self._completed_hand: dict[str, Any] | None = None
        self._hand_started_at: str | None = None
        self._hand_hole_cards: list[list[str]] = []
        self._replay_frames: list[dict[str, Any]] = []

    def can_add_player(self, seat_index: int, user_id: str, username: str, chip_stack: int) -> bool:
        if self.is_hand_running:
            return False
        if not 0 <= seat_index < self.max_seats or seat_index in self.seats:
            return False
        if not user_id or not username or isinstance(chip_stack, bool) or not isinstance(chip_stack, int):
            return False
        if not self.buy_in_min <= chip_stack <= self.buy_in_max:
            return False
        if any(player.user_id == user_id for player in self.seats.values()):
            return False
        return True

    def add_player(
        self,
        seat_index: int,
        user_id: str,
        username: str,
        chip_stack: int,
        buy_in_id: str | None = None,
        club_id: str | None = None,
    ) -> bool:
        if not self.can_add_player(seat_index, user_id, username, chip_stack):
            return False

        self.seats[seat_index] = PlayerSeat(
            user_id,
            username,
            chip_stack,
            buy_in_id=buy_in_id,
            buy_in_amount=chip_stack,
            club_id=club_id,
        )
        return True

    def start_new_hand(self) -> bool:
        if self.is_hand_running:
            return False

        active_seats = sorted(
            seat for seat, player in self.seats.items()
            if player.status == "sitting_in" and player.stack > 0
        )
        if len(active_seats) < 2:
            return False

        offset = self._button_offset % len(active_seats)
        self._player_order = active_seats[offset:] + active_seats[:offset]
        self._button_offset = (offset + 1) % len(active_seats)
        starting_stacks = [self.seats[seat].stack for seat in self._player_order]
        self.poker_game = NoLimitTexasHoldem.create_state(
            (
                Automation.ANTE_POSTING,
                Automation.BET_COLLECTION,
                Automation.BLIND_OR_STRADDLE_POSTING,
                Automation.CARD_BURNING,
                Automation.HOLE_DEALING,
                Automation.BOARD_DEALING,
                Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
                Automation.HAND_KILLING,
                Automation.CHIPS_PUSHING,
                Automation.CHIPS_PULLING,
            ),
            False,
            0,
            (self.small_blind, self.big_blind),
            self.big_blind,
            starting_stacks,
            len(starting_stacks),
        )
        self._current_hand_id = str(uuid4())
        self._completed_hand = None
        self._hand_started_at = datetime.now(timezone.utc).isoformat()
        self._hand_hole_cards = [
            [str(card) for card in cards] for cards in self.poker_game.hole_cards
        ]
        self._replay_frames = []
        self.is_hand_running = True
        self._sync_stacks()
        self._record_replay_frame(None)
        return True

    def handle_player_action(self, user_id: str, action_type: str, amount: int | None = None) -> bool:
        state = self.poker_game
        if not self.is_hand_running or state is None or state.actor_index is None:
            return False

        actor_seat = self._player_order[state.actor_index]
        if self.seats[actor_seat].user_id != user_id:
            raise ValueError("It is not this player's turn.")

        action = action_type.strip().upper()
        action_timestamp = datetime.now(timezone.utc).isoformat()
        action_amount = amount
        if action == "FOLD":
            if not state.can_fold():
                raise ValueError("Folding is not currently allowed.")
            state.fold()
        elif action in {"CHECK", "CALL"}:
            call_amount = state.checking_or_calling_amount
            action_amount = call_amount if action == "CALL" else None
            if action == "CHECK" and call_amount != 0:
                raise ValueError("Cannot check while facing a bet.")
            if not state.can_check_or_call():
                raise ValueError("Checking or calling is not currently allowed.")
            state.check_or_call()
        elif action == "RAISE":
            if isinstance(amount, bool) or not isinstance(amount, int):
                raise ValueError("RAISE requires an integer amount to raise to.")
            if not state.can_complete_bet_or_raise_to(amount):
                raise ValueError("That raise amount is not legal.")
            state.complete_bet_or_raise_to(amount)
        else:
            raise ValueError("Action must be FOLD, CHECK, CALL, or RAISE.")

        self._sync_stacks()
        self._record_replay_frame(
            {
                "user_id": user_id,
                "action": action,
                "amount": action_amount,
                "timestamp": action_timestamp,
            }
        )
        if not state.status:
            self.is_hand_running = False
            self._completed_hand = self._build_hand_summary()
            self.hand_history.append(self._completed_hand)
        return True

    @property
    def current_actor_user_id(self) -> str | None:
        state = self.poker_game
        if not self.is_hand_running or state is None or state.actor_index is None:
            return None
        return self.seats[self._player_order[state.actor_index]].user_id

    @property
    def current_call_amount(self) -> int | None:
        if not self.is_hand_running or self.poker_game is None:
            return None
        return self.poker_game.checking_or_calling_amount

    def pop_completed_hand(self) -> dict[str, Any] | None:
        hand = self._completed_hand
        self._completed_hand = None
        return hand

    def get_masked_state_for_player(self, user_id: str) -> dict[str, Any]:
        state = self.poker_game
        board_cards = []
        players = []
        active_turn_seat = None
        pots = []
        street = None

        if state is not None:
            board_cards = [str(card) for card in state.get_board_cards(0)]
            pots = [pot.amount for pot in state.pots]
            street = state.street_index
            if state.turn_index is not None:
                active_turn_seat = self._player_order[state.turn_index]

        for seat, player in sorted(self.seats.items()):
            player_view: dict[str, Any] = {
                "seat": seat,
                "user_id": player.user_id,
                "username": player.username,
                "club_id": player.club_id,
                "stack": player.stack,
                "status": player.status,
                "hole_cards": [],
            }
            if state is not None and seat in self._player_order:
                player_index = self._player_order.index(seat)
                cards = state.hole_cards[player_index]
                if player.user_id == user_id:
                    player_view["hole_cards"] = [str(card) for card in cards]
                else:
                    statuses = state.hole_card_statuses[player_index]
                    player_view["hole_cards"] = [
                        str(card) if is_public else "??"
                        for card, is_public in zip(cards, statuses)
                    ]
                player_view["in_hand"] = state.statuses[player_index]
            players.append(player_view)

        return {
            "table_id": self.table_id,
            "table_name": self.table_name,
            "is_union_table": self.union_id is not None,
            "union_id": self.union_id,
            "origin_club_id": self.origin_club_id,
            "hand_id": self._current_hand_id,
            "is_hand_running": self.is_hand_running,
            "street": street,
            "community_cards": board_cards,
            "pot": state.total_pot_amount if state is not None else 0,
            "pots": pots,
            "active_turn_seat": active_turn_seat,
            "players": players,
        }

    def _sync_stacks(self) -> None:
        if self.poker_game is None:
            return
        for player_index, seat in enumerate(self._player_order):
            self.seats[seat].stack = self.poker_game.stacks[player_index]

    def _record_replay_frame(self, last_action: dict[str, Any] | None) -> None:
        state = self.poker_game
        if state is None:
            return

        folded_player_indices = {
            operation.player_index
            for operation in state.operations
            if type(operation).__name__ == "Folding"
        }
        active_seat = (
            self._player_order[state.actor_index]
            if state.actor_index is not None
            else None
        )
        frame_players = []
        for player_index, seat in enumerate(self._player_order):
            player = self.seats[seat]
            frame_players.append(
                {
                    "user_id": player.user_id,
                    "username": player.username,
                    "club_id": player.club_id,
                    "seat": seat,
                    "stack": player.stack,
                    "hole_cards": list(self._hand_hole_cards[player_index]),
                    "mucked": False,
                    "in_hand": player_index not in folded_player_indices,
                }
            )

        timestamp = (
            last_action["timestamp"]
            if last_action is not None
            else datetime.now(timezone.utc).isoformat()
        )
        self._replay_frames.append(
            {
                "frame_index": len(self._replay_frames),
                "timestamp": timestamp,
                "street": state.street_index,
                "community_cards": [str(card) for card in state.get_board_cards(0)],
                "pot": state.total_pot_amount,
                "active_seat": active_seat,
                "last_action": last_action,
                "players": frame_players,
            }
        )

    def _build_hand_summary(self) -> dict[str, Any]:
        state = self.poker_game
        if state is None or self._current_hand_id is None:
            raise RuntimeError("Cannot summarize a hand that has not started.")

        is_showdown = any(
            type(operation).__name__ == "HoleCardsShowingOrMucking"
            for operation in state.operations
        )
        player_mucked = {
            player_index: not (
                len(state.hole_card_statuses[player_index])
                == len(self._hand_hole_cards[player_index])
                and all(state.hole_card_statuses[player_index])
            )
            for player_index in range(len(self._player_order))
        }
        for frame in self._replay_frames:
            for player_index, frame_player in enumerate(frame["players"]):
                frame_player["mucked"] = player_mucked[player_index]

        operations = []
        payouts = [0] * len(self._player_order)
        pot_distributions = []
        for operation in state.operations:
            event: dict[str, Any] = {"type": type(operation).__name__}
            player_index = getattr(operation, "player_index", None)
            if player_index is not None:
                event["user_id"] = self.seats[self._player_order[player_index]].user_id
            for field in ("amount", "amounts", "cards", "hole_cards", "pot_index"):
                value = getattr(operation, field, None)
                if value is not None:
                    if isinstance(value, (tuple, list)):
                        event[field] = [str(item) for item in value]
                    else:
                        event[field] = str(value) if field == "cards" else value
            operations.append(event)
            if type(operation).__name__ == "ChipsPulling":
                payouts[operation.player_index] += operation.amount
            elif type(operation).__name__ == "ChipsPushing":
                pot_distributions.append(
                    {
                        "pot_index": operation.pot_index,
                        "board_index": operation.board_index,
                        "hand_type_index": operation.hand_type_index,
                        "amounts": list(operation.amounts),
                    }
                )

        pot_total = state.total_pushed_amount
        rake_amount = max(pot_total - sum(payouts), 0)
        completed_at = datetime.now(timezone.utc).isoformat()

        return {
            "hand_id": self._current_hand_id,
            "table_id": self.table_id,
            "table_name": self.table_name,
            "is_union_table": self.union_id is not None,
            "union_id": self.union_id,
            "origin_club_id": self.origin_club_id,
            "started_at": self._hand_started_at,
            "timestamp": completed_at,
            "final_board": [str(card) for card in state.get_board_cards(0)],
            "board": [str(card) for card in state.get_board_cards(0)],
            "pot_total": pot_total,
            "rake_amount": rake_amount,
            "is_showdown": is_showdown,
            "winners": [
                {
                    "user_id": self.seats[self._player_order[player_index]].user_id,
                    "amount": payouts[player_index],
                }
                for player_index, amount in enumerate(payouts)
                if amount > 0
            ],
            "pot_distributions": pot_distributions,
            "deck_state": {
                "remaining_cards": [str(card) for card in state.deck_cards],
                "burn_cards": [str(card) for card in state.burn_cards],
                "mucked_cards": [str(card) for card in state.mucked_cards],
                "reserved_cards": [str(card) for card in state.reserved_cards],
            },
            "frames": self._replay_frames,
            "players": [
                {
                    "seat": seat,
                    "user_id": self.seats[seat].user_id,
                    "username": self.seats[seat].username,
                    "club_id": self.seats[seat].club_id,
                    "starting_stack": state.starting_stacks[player_index],
                    "final_stack": state.stacks[player_index],
                    "net_delta": state.payoffs[player_index],
                    "hole_cards": list(self._hand_hole_cards[player_index]),
                    "mucked": player_mucked[player_index],
                }
                for player_index, seat in enumerate(self._player_order)
            ],
            "operations": operations,
        }