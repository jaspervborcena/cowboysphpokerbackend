from __future__ import annotations

import asyncio
from collections import OrderedDict

from database import FirestoreHandRepository, NullHandRepository
from logic.connections import ConnectionManager
from logic.table_checkpoint import deserialize_first_party_table
from logic.table_game_base import TableGameBase
from logic.table_ownership import TableLease, TableLeaseManager


class ActionIdempotencyConflict(ValueError):
    pass


class TableRuntime:
    def __init__(
        self,
        club_id: str,
        game: TableGameBase,
        connections: ConnectionManager,
        repository: FirestoreHandRepository | NullHandRepository | None,
        turn_timeout_seconds: int,
        union_id: str | None = None,
        lease_manager: TableLeaseManager | None = None,
    ) -> None:
        self.club_id = club_id
        self.game = game
        self.connections = connections
        self.repository = repository
        self.turn_timeout_seconds = turn_timeout_seconds
        self.union_id = union_id
        self.connection_scope = f"union:{union_id}" if union_id else club_id
        self.lock = asyncio.Lock()
        self.lease_manager = lease_manager or TableLeaseManager()
        self._worker_id: str | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._recent_actions: OrderedDict[
            tuple[str, str], tuple[str, int | None]
        ] = OrderedDict()

    def claim_lease(self, worker_id: str, *, force_reclaim: bool = False) -> TableLease:
        lease = self.lease_manager.claim_lease(
            self.game.table_id,
            worker_id,
            force_reclaim=force_reclaim,
        )
        self._worker_id = worker_id
        return lease

    def renew_lease(self, worker_id: str, fencing_generation: int) -> TableLease:
        return self.lease_manager.renew_lease(
            self.game.table_id,
            worker_id,
            fencing_generation,
        )

    def authorize_action(self, worker_id: str, fencing_generation: int) -> TableLease:
        return self.lease_manager.authorize_action(
            self.game.table_id,
            worker_id,
            fencing_generation,
        )

    def current_lease(self) -> TableLease | None:
        return self.lease_manager._leases.get(self.game.table_id)

    async def load_persisted_state(self) -> bool:
        if self.repository is None:
            return False

        checkpoint_payload = await asyncio.to_thread(
            self.repository.get_table_checkpoint,
            "club",
            self.club_id,
            self.game.table_id,
        )
        if checkpoint_payload is None:
            return False

        checkpoint = checkpoint_payload.get("checkpoint")
        if checkpoint is None:
            return False

        restored_game = deserialize_first_party_table(checkpoint)
        self.game = restored_game

        owner_id = checkpoint_payload.get("owner_id")
        fencing_epoch = checkpoint_payload.get("fencing_epoch")
        if owner_id is None or fencing_epoch is None:
            return True

        lease = self.lease_manager.recover_lease(
            self.game.table_id,
            owner_id,
            fencing_epoch,
        )
        self._worker_id = owner_id
        return lease.owner_worker_id == owner_id and lease.fencing_generation == fencing_epoch

    async def start_hand(self, straddle_player_id: str | None = None) -> bool:
        async with self.lock:
            await self._persist_completed_hand()
            started = (
                self.game.start_new_hand()
                if straddle_player_id is None
                else self.game.start_new_hand(straddle_player_id)
            )
        if started:
            await self._broadcast_and_reset_timer()
        return started

    async def act(
        self,
        user_id: str,
        action: str,
        amount: int | None = None,
        action_id: str | None = None,
    ) -> bool:
        async with self.lock:
            replayed = False
            action_key = (user_id, action_id) if action_id is not None else None
            fingerprint = (action, amount)
            if action_key is not None and action_key in self._recent_actions:
                if self._recent_actions[action_key] != fingerprint:
                    raise ActionIdempotencyConflict(
                        "Action identifier was reused with different action details."
                    )
                self._recent_actions.move_to_end(action_key)
                replayed = True
                await self._apply_pending_pre_actions()
                await self._persist_completed_hand()
                await self.connections.broadcast_table_state(
                    self.connection_scope,
                    self.game.table_id,
                    self.game,
                )
            else:
                self.game.handle_player_action(user_id, action, amount)
                if action_key is not None:
                    self._recent_actions[action_key] = fingerprint
                    if len(self._recent_actions) > 4096:
                        self._recent_actions.popitem(last=False)
                await self._apply_pending_pre_actions()
                await self._persist_completed_hand()
        if not replayed:
            await self._broadcast_and_reset_timer()
        return replayed

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