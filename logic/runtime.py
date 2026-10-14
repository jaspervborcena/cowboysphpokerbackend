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
    ) -> None:
        self.club_id = club_id
        self.game = game
        self.connections = connections
        self.repository = repository
        self.turn_timeout_seconds = turn_timeout_seconds
        self.lock = asyncio.Lock()
        self._turn_task: asyncio.Task[None] | None = None

    async def start_hand(self) -> bool:
        async with self.lock:
            started = self.game.start_new_hand()
        if started:
            await self._broadcast_and_reset_timer()
        return started

    async def act(self, user_id: str, action: str, amount: int | None = None) -> None:
        async with self.lock:
            self.game.handle_player_action(user_id, action, amount)
            await self._persist_completed_hand()
        await self._broadcast_and_reset_timer()

    async def _persist_completed_hand(self) -> None:
        hand = self.game.pop_completed_hand()
        if hand is None:
            return
        hand["club_id"] = self.club_id
        chip_deltas = {player["user_id"]: player["net_delta"] for player in hand["players"]}
        await asyncio.to_thread(
            self.repository.persist_hand_history_and_ledgers,
            self.club_id,
            self.game.table_id,
            hand,
            chip_deltas,
        )

    async def _broadcast_and_reset_timer(self) -> None:
        await self.connections.broadcast_table_state(self.club_id, self.game.table_id, self.game)
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
                await self._persist_completed_hand()
            await self._broadcast_and_reset_timer()
        except asyncio.CancelledError:
            return