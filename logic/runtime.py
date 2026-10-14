from __future__ import annotations

import asyncio

from base_poker_game import BasePokerGame
from database import FirestoreHandRepository, NullHandRepository
from logic.connections import ConnectionManager


class TableRuntime:
    def __init__(
        self,
        club_id: str,
        game: BasePokerGame,
        connections: ConnectionManager,
        repository: FirestoreHandRepository | NullHandRepository,
        turn_timeout_seconds: int,
        union_id: str | None = None,
    ) -> None:
        self.club_id = club_id
        self.game = game
        self.connections = connections
        self.repository = repository
        self.turn_timeout_seconds = turn_timeout_seconds
        self.union_id = union_id
        self.connection_scope = f"union:{union_id}" if union_id else club_id
        self.lock = asyncio.Lock()
        self._turn_task: asyncio.Task[None] | None = None

    async def start_hand(self) -> bool:
        async with self.lock:
            await self._persist_completed_hand()
            started = self.game.start_new_hand()
        if started:
            await self._broadcast_and_reset_timer()
        return started

    async def act(self, user_id: str, action: str, amount: int | None = None) -> None:
        async with self.lock:
            self.game.handle_player_action(user_id, action, amount)
            await self._apply_pending_pre_actions()
            await self._persist_completed_hand()
        await self._broadcast_and_reset_timer()

    async def set_pre_action(self, user_id: str, option: str | None) -> str | None:
        async with self.lock:
            return self.game.set_pre_action(user_id, option)

    async def _apply_pending_pre_actions(self) -> None:
        while self.game.is_hand_running:
            actor = self.game.current_actor_user_id
            if actor is None or not self.game.get_pre_action_for_player(actor)["action"]:
                return
            if not self.game.execute_pending_pre_action(actor):
                return

    async def _persist_completed_hand(self) -> None:
        hand = self.game.peek_completed_hand()
        if hand is None:
            return
        chip_deltas = {player["user_id"]: player["net_delta"] for player in hand["players"]}
        if self.union_id is not None:
            hand["union_id"] = self.union_id
            player_clubs = {
                player["user_id"]: player["club_id"]
                for player in hand["players"]
            }
            await asyncio.to_thread(
                self.repository.persist_union_hand_history_and_ledgers,
                self.union_id,
                self.game.table_id,
                hand,
                chip_deltas,
                player_clubs,
            )
        else:
            hand["club_id"] = self.club_id
            await asyncio.to_thread(
                self.repository.persist_hand_history_and_ledgers,
                self.club_id,
                self.game.table_id,
                hand,
                chip_deltas,
            )
        self.game.acknowledge_completed_hand(hand["hand_id"])

    async def _broadcast_and_reset_timer(self) -> None:
        await self.connections.broadcast_table_state(
            self.connection_scope,
            self.game.table_id,
            self.game,
        )
        current_task = asyncio.current_task()
        if self._turn_task is not None and self._turn_task is not current_task:
            self._turn_task.cancel()
        self._turn_task = None
        actor = self.game.current_actor_user_id
        if actor is not None and self.turn_timeout_seconds > 0:
            self._turn_task = asyncio.create_task(self._timeout_turn(actor))

    async def _timeout_turn(self, expected_user_id: str) -> None:
        try:
            await asyncio.sleep(self.turn_timeout_seconds)
            async with self.lock:
                if self.game.current_actor_user_id != expected_user_id:
                    return
                action = "FOLD" if (self.game.current_call_amount or 0) > 0 else "CHECK"
                self.game.handle_player_action(expected_user_id, action)
                await self._apply_pending_pre_actions()
                await self._persist_completed_hand()
            await self._broadcast_and_reset_timer()
        except asyncio.CancelledError:
            return