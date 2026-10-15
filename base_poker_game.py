from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pokerkit import Automation, NoLimitTexasHoldem
from logic.game_catalog import SUPPORTED_GAME_ID, is_game_create_supported


def calculate_hand_settlement_finances(
    total_pot: int,
    dealt_flop: bool = False,
    rake_cfg: dict[str, Any] | None = None,
    jackpot_cfg: dict[str, Any] | None = None,
    showdown_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the house take and progressive-pool contributions for a completed hand.

    The helper is intentionally generic so the same logic can be used by the game engine and
    by Firestore-backed settlement services.
    """

    pot_total = max(0, int(total_pot))
    house_rake = 0
    jackpot_drop = 0

    if rake_cfg:
        percentage = rake_cfg.get("percentage", rake_cfg.get("rake_rate", rake_cfg.get("rate", 0)))
        cap_amount = rake_cfg.get("cap_amount", rake_cfg.get("rake_cap", 0))
        no_flop_no_drop = bool(rake_cfg.get("no_flop_no_drop", True))
        if percentage and (dealt_flop or not no_flop_no_drop):
            percent_value = float(percentage)
            raw_rake = pot_total * percent_value
            if cap_amount:
                house_rake = min(int(raw_rake), int(cap_amount))
            else:
                house_rake = int(raw_rake)

    playable_pot = max(0, pot_total - house_rake)

    if jackpot_cfg and jackpot_cfg.get("enabled"):
        threshold = int(jackpot_cfg.get("min_pot_threshold", jackpot_cfg.get("min_pot", 0)))
        if playable_pot >= threshold:
            fee_amount = int(jackpot_cfg.get("fee_amount", jackpot_cfg.get("amount", 0)))
            jackpot_drop = max(0, min(fee_amount, playable_pot))

    net_winning_pot = max(0, playable_pot - jackpot_drop)
    return {
        "house_rake": house_rake,
        "jackpot_drop": jackpot_drop,
        "playable_pot": playable_pot,
        "net_winning_pot": net_winning_pot,
        "showdown_data": showdown_data,
    }


@dataclass
class TableSettings:
    jackpot_enabled: bool = False
    jackpot_fee: int = 0
    jackpot_min_pot: int = 0
    jackpot_qualifying_hand: str | None = None
    rake_rate: float = 0.0
    rake_cap: int = 0
    rake_min_pot: int = 0
    no_flop_no_drop: bool = True
    hand_of_day_enabled: bool = False
    hand_of_day_target: int = 0
    hand_of_day_prize: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "jackpot_enabled": self.jackpot_enabled,
            "jackpot_fee": self.jackpot_fee,
            "jackpot_min_pot": self.jackpot_min_pot,
            "jackpot_qualifying_hand": self.jackpot_qualifying_hand,
            "rake_rate": self.rake_rate,
            "rake_cap": self.rake_cap,
            "rake_min_pot": self.rake_min_pot,
            "no_flop_no_drop": self.no_flop_no_drop,
            "hand_of_day_enabled": self.hand_of_day_enabled,
            "hand_of_day_target": self.hand_of_day_target,
            "hand_of_day_prize": self.hand_of_day_prize,
        }


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
        game_id: str = SUPPORTED_GAME_ID,
    ) -> None:
        if not is_game_create_supported(game_id):
            raise ValueError("This game is not supported for table creation.")
        if small_blind <= 0 or big_blind < small_blind:
            raise ValueError("Blinds must be positive and the big blind must be at least the small blind.")
        if buy_in_min <= 0 or buy_in_max < buy_in_min:
            raise ValueError("Buy-in limits are invalid.")
        if max_seats < 2:
            raise ValueError("A table must allow at least two seats.")

        self.table_id = table_id
        self.game_id = game_id
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.buy_in_min = buy_in_min
        self.buy_in_max = buy_in_max
        self.max_seats = max_seats
        self.union_id = union_id
        self.origin_club_id = origin_club_id
        self.table_name = table_name or table_id
        self.table_settings = TableSettings()
        self.jackpot_pool = 0
        self.rake_pool = 0
        self.hand_of_day_pool = 0
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
        self._pending_pre_actions: dict[str, str] = {}

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

    def update_table_settings(
        self,
        *,
        jackpot_enabled: bool | None = None,
        jackpot_fee: int | None = None,
        jackpot_min_pot: int | None = None,
        jackpot_qualifying_hand: str | None = None,
        rake_rate: float | None = None,
        percentage: float | None = None,
        rake_cap: int | None = None,
        cap_amount: int | None = None,
        rake_min_pot: int | None = None,
        no_flop_no_drop: bool | None = None,
        hand_of_day_enabled: bool | None = None,
        hand_of_day_target: int | None = None,
        hand_of_day_prize: int | None = None,
    ) -> dict[str, Any]:
        if jackpot_enabled is not None:
            self.table_settings.jackpot_enabled = bool(jackpot_enabled)
        if jackpot_fee is not None:
            if isinstance(jackpot_fee, bool) or not isinstance(jackpot_fee, int) or jackpot_fee < 0:
                raise ValueError("jackpot_fee must be a non-negative integer.")
            self.table_settings.jackpot_fee = jackpot_fee
        if jackpot_min_pot is not None:
            if isinstance(jackpot_min_pot, bool) or not isinstance(jackpot_min_pot, int) or jackpot_min_pot < 0:
                raise ValueError("jackpot_min_pot must be a non-negative integer.")
            self.table_settings.jackpot_min_pot = jackpot_min_pot
        if jackpot_qualifying_hand is not None:
            normalized = str(jackpot_qualifying_hand).strip()
            if not normalized:
                raise ValueError("jackpot_qualifying_hand cannot be blank.")
            self.table_settings.jackpot_qualifying_hand = normalized
        if rake_rate is not None:
            if isinstance(rake_rate, bool) or not isinstance(rake_rate, (int, float)) or not 0 <= rake_rate <= 0.10:
                raise ValueError("rake_rate must be between 0 and 0.10.")
            self.table_settings.rake_rate = float(rake_rate)
        if percentage is not None:
            if isinstance(percentage, bool) or not isinstance(percentage, (int, float)) or not 0 <= percentage <= 0.10:
                raise ValueError("percentage must be between 0 and 0.10.")
            self.table_settings.rake_rate = float(percentage)
        if rake_cap is not None:
            if isinstance(rake_cap, bool) or not isinstance(rake_cap, int) or rake_cap < 0:
                raise ValueError("rake_cap must be a non-negative integer.")
            self.table_settings.rake_cap = rake_cap
        if cap_amount is not None:
            if isinstance(cap_amount, bool) or not isinstance(cap_amount, int) or cap_amount < 0:
                raise ValueError("cap_amount must be a non-negative integer.")
            self.table_settings.rake_cap = cap_amount
        if rake_min_pot is not None:
            if isinstance(rake_min_pot, bool) or not isinstance(rake_min_pot, int) or rake_min_pot < 0:
                raise ValueError("rake_min_pot must be a non-negative integer.")
            self.table_settings.rake_min_pot = rake_min_pot
        if no_flop_no_drop is not None:
            if not isinstance(no_flop_no_drop, bool):
                raise ValueError("no_flop_no_drop must be a boolean.")
            self.table_settings.no_flop_no_drop = bool(no_flop_no_drop)
        if hand_of_day_enabled is not None:
            self.table_settings.hand_of_day_enabled = bool(hand_of_day_enabled)
        if hand_of_day_target is not None:
            if isinstance(hand_of_day_target, bool) or not isinstance(hand_of_day_target, int) or hand_of_day_target < 0:
                raise ValueError("hand_of_day_target must be a non-negative integer.")
            self.table_settings.hand_of_day_target = hand_of_day_target
        if hand_of_day_prize is not None:
            if isinstance(hand_of_day_prize, bool) or not isinstance(hand_of_day_prize, int) or hand_of_day_prize < 0:
                raise ValueError("hand_of_day_prize must be a non-negative integer.")
            self.table_settings.hand_of_day_prize = hand_of_day_prize
        return self.table_settings.as_dict()

    def calculate_jackpot_accounting(self, pot_total: int) -> dict[str, Any]:
        settings = self.table_settings
        eligible = (
            settings.jackpot_enabled
            and settings.jackpot_fee > 0
            and pot_total >= settings.jackpot_min_pot
        )
        fee_collected = min(settings.jackpot_fee, max(pot_total, 0)) if eligible else 0
        if eligible:
            self.jackpot_pool += fee_collected
        return {
            "eligible": eligible,
            "fee_collected": fee_collected,
            "qualifying_hand": settings.jackpot_qualifying_hand if eligible else None,
            "pool_after_hand": self.jackpot_pool,
        }

    def calculate_rake_accounting(
        self,
        pot_total: int,
        *,
        dealt_flop: bool = True,
    ) -> dict[str, Any]:
        settings = self.table_settings
        eligible = (
            settings.rake_rate > 0
            and pot_total >= settings.rake_min_pot
            and (dealt_flop or not settings.no_flop_no_drop)
        )
        rake_amount = 0
        if eligible:
            rake_amount = int(math.floor(pot_total * settings.rake_rate))
            if settings.rake_cap > 0:
                rake_amount = min(rake_amount, settings.rake_cap)
            self.rake_pool += rake_amount
        return {
            "eligible": eligible,
            "rake_amount": rake_amount,
            "effective_rate": settings.rake_rate,
            "pool_after_hand": self.rake_pool,
        }

    def calculate_hand_of_day_accounting(self, pot_total: int) -> dict[str, Any]:
        settings = self.table_settings
        eligible = (
            settings.hand_of_day_enabled
            and settings.hand_of_day_target > 0
            and pot_total >= settings.hand_of_day_target
        )
        prize_amount = settings.hand_of_day_prize if eligible else 0
        return {
            "eligible": eligible,
            "target_pot": settings.hand_of_day_target,
            "prize_amount": prize_amount,
            "pool_after_hand": self.hand_of_day_pool,
        }

    def start_new_hand(self) -> bool:
        if self.is_hand_running:
            return False

        active_seats = sorted(
            seat for seat, player in self.seats.items()
            if player.status == "sitting_in" and player.stack > 0
        )
        if len(active_seats) < 2:
            return False

        self._pending_pre_actions.clear()

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

    def peek_completed_hand(self) -> dict[str, Any] | None:
        return self._completed_hand

    def acknowledge_completed_hand(self, hand_id: str) -> bool:
        if self._completed_hand is None or self._completed_hand.get("hand_id") != hand_id:
            return False
        self._completed_hand = None
        return True

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
            "game_id": self.game_id,
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
            "table_settings": self.table_settings.as_dict(),
            "pre_action": self._pending_pre_actions.get(user_id),
            "players": players,
        }

    def get_pre_action_for_player(self, user_id: str) -> dict[str, Any]:
        return {
            "action": self._pending_pre_actions.get(user_id),
            "requires_turn": self.current_actor_user_id == user_id,
        }

    def set_pre_action(self, user_id: str, option: str | None) -> str | None:
        if option is None:
            self._pending_pre_actions.pop(user_id, None)
            return None
        if not self.is_hand_running:
            raise ValueError("Pre-actions can only be set during a running hand.")
        if user_id not in {player.user_id for player in self.seats.values()}:
            raise ValueError("A seated player account is required to set a pre-action.")
        if self.current_actor_user_id == user_id:
            raise ValueError("Pre-actions can only be set when it is not your turn.")

        normalized = str(option).strip().upper().replace(" ", "_")
        if normalized not in {"CHECK_FOLD", "FOLD", "CALL_ANY"}:
            raise ValueError("Pre-action must be CHECK_FOLD, FOLD, CALL_ANY, or null.")
        self._pending_pre_actions[user_id] = normalized
        return normalized

    def execute_pending_pre_action(self, user_id: str) -> bool:
        option = self._pending_pre_actions.pop(user_id, None)
        if option is None or self.current_actor_user_id != user_id:
            return False

        call_amount = self.current_call_amount or 0
        if option == "CHECK_FOLD":
            action = "CHECK" if call_amount == 0 else "FOLD"
        elif option == "FOLD":
            action = "FOLD"
        else:
            action = "CALL" if call_amount > 0 else "CHECK"
        return self.handle_player_action(user_id, action)

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

        pot_total = sum(payouts)
        rake = self.calculate_rake_accounting(
            pot_total,
            dealt_flop=len(list(state.get_board_cards(0))) >= 3,
        )
        jackpot = self.calculate_jackpot_accounting(
            max(pot_total - rake["rake_amount"], 0)
        )
        hand_of_day = self.calculate_hand_of_day_accounting(pot_total)
        hand_of_day_prize = hand_of_day["prize_amount"] if hand_of_day["eligible"] else 0
        house_take = min(pot_total, rake["rake_amount"] + jackpot["fee_collected"])
        player_payout_total = max(pot_total - house_take, 0)
        winner_total = pot_total
        winner_entries = [
            {
                "user_id": self.seats[self._player_order[player_index]].user_id,
                "amount": amount,
            }
            for player_index, amount in enumerate(payouts)
            if amount > 0
        ]
        adjusted_winners = []
        if winner_total > 0:
            remaining = player_payout_total
            for index, entry in enumerate(winner_entries):
                if index == len(winner_entries) - 1:
                    adjusted_amount = remaining
                else:
                    adjusted_amount = int(math.floor((entry["amount"] / winner_total) * player_payout_total))
                    remaining -= adjusted_amount
                adjusted_winners.append(
                    {
                        "user_id": entry["user_id"],
                        "amount": adjusted_amount,
                    }
                )
        else:
            adjusted_winners = []

        adjusted_awards = {
            entry["user_id"]: entry["amount"] for entry in adjusted_winners
        }
        settled_players = []
        for player_index, seat in enumerate(self._player_order):
            player = self.seats[seat]
            gross_award = payouts[player_index]
            net_award = adjusted_awards.get(player.user_id, 0)
            award_delta = net_award - gross_award
            final_stack = state.stacks[player_index] + award_delta
            net_delta = state.payoffs[player_index] + award_delta
            player.stack = final_stack
            settled_players.append(
                {
                    "seat": seat,
                    "user_id": player.user_id,
                    "username": player.username,
                    "club_id": player.club_id,
                    "starting_stack": state.starting_stacks[player_index],
                    "final_stack": final_stack,
                    "net_delta": net_delta,
                    "hole_cards": list(self._hand_hole_cards[player_index]),
                    "mucked": player_mucked[player_index],
                }
            )
        if self._replay_frames:
            final_frame_players = {
                player["user_id"]: player["stack"]
                for player in self._replay_frames[-1]["players"]
            }
            for player in settled_players:
                final_frame_players[player["user_id"]] = player["final_stack"]
            for frame_player in self._replay_frames[-1]["players"]:
                frame_player["stack"] = final_frame_players[frame_player["user_id"]]
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
            "rake_amount": rake["rake_amount"],
            "rake": rake,
            "jackpot": jackpot,
            "hand_of_the_day": hand_of_day,
            "settlement": {
                "pot_before_fees": pot_total,
                "rake_amount": rake["rake_amount"],
                "jackpot_fee_collected": jackpot["fee_collected"],
                "house_take": house_take,
                "player_payout_total": player_payout_total,
                "adjusted_winners": adjusted_winners,
            },
            "is_showdown": is_showdown,
            "winners": adjusted_winners,
            "pot_distributions": pot_distributions,
            "deck_state": {
                "remaining_cards": [str(card) for card in state.deck_cards],
                "burn_cards": [str(card) for card in state.burn_cards],
                "mucked_cards": [str(card) for card in state.mucked_cards],
                "reserved_cards": [str(card) for card in state.reserved_cards],
            },
            "frames": self._replay_frames,
            "players": settled_players,
            "operations": operations,
        }