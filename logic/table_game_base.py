from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from logic.game_catalog import SUPPORTED_GAME_ID, is_game_create_supported


def calculate_hand_settlement_finances(
    total_pot: int,
    dealt_flop: bool = False,
    rake_cfg: dict[str, Any] | None = None,
    jackpot_cfg: dict[str, Any] | None = None,
    showdown_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pot_total = max(0, int(total_pot))
    house_rake = 0
    jackpot_drop = 0

    if rake_cfg:
        percentage = rake_cfg.get("percentage", rake_cfg.get("rake_rate", rake_cfg.get("rate", 0)))
        cap_amount = rake_cfg.get("cap_amount", rake_cfg.get("rake_cap", 0))
        no_flop_no_drop = bool(rake_cfg.get("no_flop_no_drop", True))
        if percentage and (dealt_flop or not no_flop_no_drop):
            raw_rake = pot_total * float(percentage)
            house_rake = int(raw_rake) if not cap_amount else min(int(raw_rake), int(cap_amount))

    playable_pot = max(0, pot_total - house_rake)
    if jackpot_cfg and jackpot_cfg.get("enabled"):
        threshold = int(jackpot_cfg.get("min_pot_threshold", jackpot_cfg.get("min_pot", 0)))
        if playable_pot >= threshold:
            fee_amount = int(jackpot_cfg.get("fee_amount", jackpot_cfg.get("amount", 0)))
            jackpot_drop = max(0, min(fee_amount, playable_pot))

    return {
        "house_rake": house_rake,
        "jackpot_drop": jackpot_drop,
        "playable_pot": playable_pot,
        "net_winning_pot": max(0, playable_pot - jackpot_drop),
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
    new_to_table: bool = False
    accept_big_blind_post: bool = False


class TableGameBase:
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
        theme_id: str = "standard",
        union_code: str | None = None,
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
        self.theme_id = theme_id
        self.union_code = union_code
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
        self._has_started_hand = False

    def can_add_player(
        self, seat_index: int, user_id: str, username: str, chip_stack: int
    ) -> bool:
        if self.is_hand_running:
            return False
        if not 0 <= seat_index < self.max_seats or seat_index in self.seats:
            return False
        if not user_id or not username or isinstance(chip_stack, bool) or not isinstance(chip_stack, int):
            return False
        if not self.buy_in_min <= chip_stack <= self.buy_in_max:
            return False
        return not any(player.user_id == user_id for player in self.seats.values())

    def add_player(
        self,
        seat_index: int,
        user_id: str,
        username: str,
        chip_stack: int,
        buy_in_id: str | None = None,
        club_id: str | None = None,
        accept_big_blind_post: bool = False,
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
            new_to_table=self._has_started_hand,
            accept_big_blind_post=accept_big_blind_post,
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
            self.table_settings.no_flop_no_drop = no_flop_no_drop
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
        self, pot_total: int, *, dealt_flop: bool = True
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
        return {
            "eligible": eligible,
            "target_pot": settings.hand_of_day_target,
            "prize_amount": settings.hand_of_day_prize if eligible else 0,
            "pool_after_hand": self.hand_of_day_pool,
        }

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