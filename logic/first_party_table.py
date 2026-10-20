from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from game_engine import (
    Deck,
    NLHHandState,
    NLHStreet,
    PlayerSeed,
    PokerAction,
    PokerActionKind,
    PokerEventKind,
    PokerTableConfig,
    apply_nlh_action,
    start_nlh_hand,
    visible_hole_cards,
)
from logic.table_game_base import TableGameBase


def _attribute_rake_by_contribution(
    rake_amount: int,
    contributions: dict[str, int],
    seat_order: tuple[str, ...],
) -> dict[str, int]:
    return _allocate_amount_by_weight(rake_amount, contributions, seat_order)


def _allocate_amount_by_weight(
    amount: int,
    weights: dict[str, int],
    seat_order: tuple[str, ...],
) -> dict[str, int]:
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
        raise ValueError("Amount must be a non-negative integer.")
    if len(set(seat_order)) != len(seat_order) or set(weights) != set(seat_order):
        raise ValueError("Seat order must contain each weighted player exactly once.")
    if any(
        isinstance(weight, bool) or not isinstance(weight, int) or weight < 0
        for weight in weights.values()
    ):
        raise ValueError("Weights must be non-negative integers.")

    total_weight = sum(weights.values())
    if amount > total_weight:
        raise ValueError("Allocated amount cannot exceed the total weight.")
    if total_weight == 0:
        return {player_id: 0 for player_id in seat_order}

    shares: dict[str, int] = {}
    remainders: dict[str, int] = {}
    for player_id, weight in weights.items():
        shares[player_id], remainders[player_id] = divmod(
            amount * weight, total_weight
        )

    remaining_chips = amount - sum(shares.values())
    remainder_order = sorted(
        seat_order,
        key=lambda player_id: (-remainders[player_id], seat_order.index(player_id)),
    )
    for player_id in remainder_order[:remaining_chips]:
        shares[player_id] += 1
    return shares


def _preflop_folded_player_ids(
    replay_frames: list[dict[str, Any]],
) -> set[str]:
    return {
        str(last_action["user_id"])
        for frame in replay_frames
        if isinstance((last_action := frame.get("last_action")), dict)
        and last_action.get("action") == "FOLD"
        and last_action.get("street") == int(NLHStreet.PREFLOP)
        and last_action.get("user_id") is not None
    }


class FirstPartyNLHGame(TableGameBase):
    """Table-facing adapter for the first-party NLH engine."""

    def start_new_hand(self, straddle_player_id: str | None = None) -> bool:
        if self.is_hand_running:
            return False

        active_seats = sorted(
            seat for seat, player in self.seats.items()
            if player.status == "sitting_in" and player.stack > 0
        )
        if len(active_seats) < 2:
            return False

        offset = self._button_offset % len(active_seats)
        button_order = active_seats[offset:] + active_seats[:offset]
        next_button_offset = (offset + 1) % len(active_seats)
        players = tuple(
            PlayerSeed(
                player_id=self.seats[seat].user_id,
                seat_index=seat,
                starting_stack=self.seats[seat].stack,
                display_name=self.seats[seat].username,
                new_to_table=self.seats[seat].new_to_table,
                accept_big_blind_post=self.seats[seat].accept_big_blind_post,
            )
            for seat in active_seats
        )
        hand_id = str(uuid4())
        hand_started_at = datetime.now(timezone.utc).isoformat()
        transition = start_nlh_hand(
            self.table_id,
            hand_id,
            PokerTableConfig(
                self.game_id,
                self.small_blind,
                self.big_blind,
                self.max_seats,
            ),
            players,
            button_order[0],
            Deck(),
            straddle_player_id,
        )
        dealt_player_ids = {player.player_id for player in transition.state.players}
        dealt_seats = [
            seat for seat in button_order
            if self.seats[seat].user_id in dealt_player_ids
        ]
        self._pending_pre_actions.clear()
        self._button_offset = next_button_offset
        self._player_order = dealt_seats
        self._current_hand_id = hand_id
        self._completed_hand = None
        self._hand_started_at = hand_started_at
        self._replay_frames = []
        self.poker_game = transition.state
        self._engine_events = list(transition.events)
        for player in self.seats.values():
            if player.user_id in dealt_player_ids:
                player.new_to_table = False
                player.accept_big_blind_post = False
        self._has_started_hand = True
        players_by_id = {player.player_id: player for player in transition.state.players}
        self._hand_hole_cards = [
            [
                players_by_id[self.seats[seat].user_id].hole_cards[0].code,
                players_by_id[self.seats[seat].user_id].hole_cards[1].code,
            ]
            for seat in self._player_order
        ]
        self.is_hand_running = transition.state.street is not NLHStreet.COMPLETE
        self._sync_stacks()
        self._record_replay_frame(None)
        if not self.is_hand_running:
            self._completed_hand = self._build_hand_summary()
            self.hand_history.append(self._completed_hand)
        return True

    def handle_player_action(
        self, user_id: str, action_type: str, amount: int | None = None
    ) -> bool:
        state = self.poker_game
        if not self.is_hand_running or not isinstance(state, NLHHandState):
            return False
        if state.actor_player_id != user_id:
            raise ValueError("It is not this player's turn.")

        action = action_type.strip().upper()
        kind_by_name = {
            "FOLD": PokerActionKind.FOLD,
            "CHECK": PokerActionKind.CHECK,
            "CALL": PokerActionKind.CALL,
            "RAISE": PokerActionKind.RAISE,
        }
        kind = kind_by_name.get(action)
        if kind is None:
            raise ValueError("Action must be FOLD, CHECK, CALL, or RAISE.")
        actor = next(player for player in state.players if player.player_id == user_id)
        action_amount = amount
        if kind is PokerActionKind.CALL:
            action_amount = min(
                max(0, state.current_bet - actor.street_contribution), actor.stack
            )
        elif kind is not PokerActionKind.RAISE:
            action_amount = None

        updated = apply_nlh_action(
            state,
            PokerAction(user_id, kind, amount if kind is PokerActionKind.RAISE else None),
        )
        self.poker_game = updated.state
        self._engine_events.extend(updated.events)
        self._sync_stacks()
        self._record_replay_frame(
            {
                "user_id": user_id,
                "action": action,
                "amount": action_amount,
                "street": int(state.street),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.is_hand_running = updated.state.street is not NLHStreet.COMPLETE
        if not self.is_hand_running:
            self._completed_hand = self._build_hand_summary()
            self.hand_history.append(self._completed_hand)
        return True

    @property
    def current_actor_user_id(self) -> str | None:
        state = self.poker_game
        if not self.is_hand_running or not isinstance(state, NLHHandState):
            return None
        return state.actor_player_id

    @property
    def current_call_amount(self) -> int | None:
        state = self.poker_game
        if not self.is_hand_running or not isinstance(state, NLHHandState):
            return None
        if state.actor_player_id is None:
            return 0
        actor = next(
            player for player in state.players
            if player.player_id == state.actor_player_id
        )
        return max(0, state.current_bet - actor.street_contribution)

    def get_masked_state_for_player(self, user_id: str) -> dict[str, Any]:
        state = self.poker_game
        players_by_id = (
            {player.player_id: player for player in state.players}
            if isinstance(state, NLHHandState)
            else {}
        )
        players = []
        for seat, player in sorted(self.seats.items()):
            engine_player = players_by_id.get(player.user_id)
            hole_cards: list[str] = []
            if engine_player is not None:
                hole_cards = list(
                    visible_hole_cards(
                        engine_player.player_id,
                        engine_player.hole_cards,
                        user_id,
                        publicly_revealed=(
                            engine_player.player_id in state.publicly_revealed_player_ids
                        ),
                    )
                )
            player_view = {
                "seat": seat,
                "user_id": player.user_id,
                "username": player.username,
                "club_id": player.club_id,
                "stack": player.stack,
                "status": player.status,
                "hole_cards": hole_cards,
                "waiting_for_big_blind": player.new_to_table and engine_player is None,
            }
            if engine_player is not None:
                player_view["in_hand"] = not engine_player.folded
            players.append(player_view)

        community_cards = []
        street = None
        active_turn_seat = None
        pot = 0
        pots: list[int] = []
        if isinstance(state, NLHHandState):
            community_cards = [card.code for card in state.community_cards]
            street = int(state.street)
            pot = sum(player.total_contribution for player in state.players)
            if state.actor_player_id is not None:
                active_turn_seat = next(
                    player.seat_index
                    for player in state.players
                    if player.player_id == state.actor_player_id
                )
            levels = sorted(
                {
                    player.total_contribution for player in state.players
                    if player.total_contribution
                }
            )
            previous = 0
            for level in levels:
                contributors = sum(
                    player.total_contribution >= level for player in state.players
                )
                pots.append((level - previous) * contributors)
                previous = level

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
            "community_cards": community_cards,
            "pot": pot,
            "pots": pots,
            "active_turn_seat": active_turn_seat,
            "table_settings": self.table_settings.as_dict(),
            "pre_action": self._pending_pre_actions.get(user_id),
            "players": players,
        }

    def _sync_stacks(self) -> None:
        state = self.poker_game
        if not isinstance(state, NLHHandState):
            return
        for engine_player in state.players:
            self.seats[engine_player.seat_index].stack = engine_player.stack

    def _record_replay_frame(self, last_action: dict[str, Any] | None) -> None:
        state = self.poker_game
        if not isinstance(state, NLHHandState):
            return
        players_by_id = {player.player_id: player for player in state.players}
        frame_players = []
        for seat in self._player_order:
            player = self.seats[seat]
            engine_player = players_by_id[player.user_id]
            frame_players.append(
                {
                    "user_id": player.user_id,
                    "username": player.username,
                    "club_id": player.club_id,
                    "seat": seat,
                    "stack": player.stack,
                    "hole_cards": [card.code for card in engine_player.hole_cards],
                    "mucked": player.user_id not in state.publicly_revealed_player_ids,
                    "in_hand": not engine_player.folded,
                }
            )
        active_seat = next(
            (
                player.seat_index
                for player in state.players
                if player.player_id == state.actor_player_id
            ),
            None,
        )
        self._replay_frames.append(
            {
                "frame_index": len(self._replay_frames),
                "timestamp": (
                    last_action["timestamp"]
                    if last_action is not None
                    else datetime.now(timezone.utc).isoformat()
                ),
                "street": int(state.street),
                "community_cards": [card.code for card in state.community_cards],
                "pot": sum(player.total_contribution for player in state.players),
                "active_seat": active_seat,
                "last_action": last_action,
                "players": frame_players,
            }
        )

    def _build_hand_summary(self) -> dict[str, Any]:
        state = self.poker_game
        if (
            not isinstance(state, NLHHandState)
            or state.outcome is None
            or self._current_hand_id is None
        ):
            raise RuntimeError("Cannot summarize a hand that has not completed.")

        outcome = state.outcome
        pot_total = outcome.total_pot
        gross_awards = {award.player_id: award.amount for award in outcome.gross_awards}
        player_mucked = {
            player.player_id: player.player_id not in state.publicly_revealed_player_ids
            for player in state.players
        }
        for frame in self._replay_frames:
            for frame_player in frame["players"]:
                frame_player["mucked"] = player_mucked[frame_player["user_id"]]

        rake = self.calculate_rake_accounting(
            pot_total, dealt_flop=len(state.community_cards) >= 3
        )
        uncalled_returns = {
            award.player_id: award.amount for award in outcome.uncalled_returns
        }
        contestable_contributions = {
            player.player_id: player.total_contribution
            - uncalled_returns.get(player.player_id, 0)
            for player in state.players
        }
        preflop_folded = _preflop_folded_player_ids(self._replay_frames)
        rake_eligible = {
            player.player_id: player.player_id not in preflop_folded
            for player in state.players
        }
        eligible_contributions = {
            player_id: contribution
            for player_id, contribution in contestable_contributions.items()
            if rake_eligible[player_id]
        }
        eligible_seat_order = tuple(
            self.seats[seat].user_id
            for seat in self._player_order
            if rake_eligible[self.seats[seat].user_id]
        )
        attributed_eligible = _attribute_rake_by_contribution(
            rake["rake_amount"], eligible_contributions, eligible_seat_order
        )
        rake_attributions = {
            player.player_id: attributed_eligible.get(player.player_id, 0)
            for player in state.players
        }
        jackpot = self.calculate_jackpot_accounting(
            max(pot_total - rake["rake_amount"], 0)
        )
        hand_of_day = self.calculate_hand_of_day_accounting(pot_total)
        house_take = min(pot_total, rake["rake_amount"] + jackpot["fee_collected"])
        player_payout_total = max(pot_total - house_take, 0)
        winner_order = tuple(
            self.seats[seat].user_id
            for seat in self._player_order
            if gross_awards.get(self.seats[seat].user_id, 0) > 0
        )
        winner_gross_awards = {
            player_id: gross_awards[player_id] for player_id in winner_order
        }
        rake_deductions = _allocate_amount_by_weight(
            rake["rake_amount"], winner_gross_awards, winner_order
        )
        after_rake_awards = {
            player_id: winner_gross_awards[player_id] - rake_deductions[player_id]
            for player_id in winner_order
        }
        bbj_drop_deductions = _allocate_amount_by_weight(
            jackpot["fee_collected"], after_rake_awards, winner_order
        )
        adjusted_winners = [
            {
                "user_id": player_id,
                "gross_award": winner_gross_awards[player_id],
                "rake_deduction": rake_deductions[player_id],
                "bbj_drop_deduction": bbj_drop_deductions[player_id],
                "amount": (
                    after_rake_awards[player_id]
                    - bbj_drop_deductions[player_id]
                ),
            }
            for player_id in winner_order
        ]
        adjusted_awards = {
            entry["user_id"]: entry["amount"] for entry in adjusted_winners
        }
        rake_deductions_by_player = {
            player["user_id"]: player["rake_deduction"]
            for player in adjusted_winners
        }
        bbj_deductions_by_player = {
            player["user_id"]: player["bbj_drop_deduction"]
            for player in adjusted_winners
        }
        players_by_id = {player.player_id: player for player in state.players}
        settled_players = []
        for seat in self._player_order:
            seat_player = self.seats[seat]
            engine_player = players_by_id[seat_player.user_id]
            gross_award = gross_awards.get(seat_player.user_id, 0)
            final_stack = (
                engine_player.stack
                + adjusted_awards.get(seat_player.user_id, 0)
                - gross_award
            )
            seat_player.stack = final_stack
            settled_players.append(
                {
                    "seat": seat,
                    "user_id": seat_player.user_id,
                    "username": seat_player.username,
                    "club_id": seat_player.club_id,
                    "starting_stack": engine_player.starting_stack,
                    "final_stack": final_stack,
                    "contestable_contribution": contestable_contributions[
                        seat_player.user_id
                    ],
                    "uncalled_return": uncalled_returns.get(seat_player.user_id, 0),
                    "gross_award": gross_award,
                    "payout_after_fees": adjusted_awards.get(
                        seat_player.user_id, 0
                    ),
                    "rake_deduction": rake_deductions_by_player.get(
                        seat_player.user_id, 0
                    ),
                    "bbj_drop_deduction": bbj_deductions_by_player.get(
                        seat_player.user_id, 0
                    ),
                    "rake_eligible": rake_eligible[seat_player.user_id],
                    "rake_attributed": rake_attributions[seat_player.user_id],
                    "net_delta": final_stack - engine_player.starting_stack,
                    "hole_cards": [card.code for card in engine_player.hole_cards],
                    "mucked": player_mucked[seat_player.user_id],
                }
            )
        if self._replay_frames:
            final_stacks = {
                player["user_id"]: player["final_stack"]
                for player in settled_players
            }
            for frame_player in self._replay_frames[-1]["players"]:
                frame_player["stack"] = final_stacks[frame_player["user_id"]]

        operation_names = {
            PokerEventKind.PLAYER_FOLDED: "Folding",
            PokerEventKind.PLAYER_CHECKED: "CheckingOrCalling",
            PokerEventKind.PLAYER_CALLED: "CheckingOrCalling",
            PokerEventKind.PLAYER_RAISED: "BettingOrRaisingTo",
            PokerEventKind.PLAYER_WENT_ALL_IN: "BettingOrRaisingTo",
            PokerEventKind.SMALL_BLIND_POSTED: "BlindOrStraddlePosting",
            PokerEventKind.BIG_BLIND_POSTED: "BlindOrStraddlePosting",
            PokerEventKind.STRADDLE_POSTED: "BlindOrStraddlePosting",
            PokerEventKind.NEW_PLAYER_POSTED_BIG_BLIND: "BlindOrStraddlePosting",
            PokerEventKind.CARDS_DEALT: "HoleDealing",
            PokerEventKind.BOARD_DEALT: "BoardDealing",
            PokerEventKind.STREET_CHANGED: "StreetChanged",
            PokerEventKind.HAND_COMPLETED: "HandCompleted",
            PokerEventKind.NEW_PLAYER_WAITING_FOR_BIG_BLIND: "PlayerWaitingForBigBlind",
        }
        operations = []
        for event in self._engine_events:
            operation: dict[str, Any] = {
                "type": operation_names[event.kind],
                "event": event.kind.value,
            }
            if event.player_id is not None:
                operation["user_id"] = event.player_id
            if event.amount is not None:
                operation["amount"] = event.amount
            if event.cards:
                operation["cards"] = [card.code for card in event.cards]
            operations.append(operation)

        amounts = [
            gross_awards.get(self.seats[seat].user_id, 0)
            for seat in self._player_order
        ]
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
            "final_board": [card.code for card in state.community_cards],
            "board": [card.code for card in state.community_cards],
            "pot_total": pot_total,
            "uncalled_returns": [
                {"user_id": award.player_id, "amount": award.amount}
                for award in outcome.uncalled_returns
            ],
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
            "is_showdown": any(
                player.player_id in state.publicly_revealed_player_ids
                and not player.folded
                for player in state.players
            ),
            "winners": adjusted_winners,
            "pot_distributions": ([{
                "pot_index": 0,
                "board_index": 0,
                "hand_type_index": 0,
                "amounts": amounts,
            }] if pot_total else []),
            "deck_state": {
                "remaining_cards": [card.code for card in state.deck.remaining_cards],
                "burn_cards": [card.code for card in state.deck.burned_cards],
                "mucked_cards": [
                    card.code
                    for player in state.players
                    if player.folded
                    for card in player.hole_cards
                ],
                "reserved_cards": [],
            },
            "frames": self._replay_frames,
            "players": settled_players,
            "operations": operations,
        }