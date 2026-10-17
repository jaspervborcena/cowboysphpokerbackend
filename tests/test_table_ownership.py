from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from database import (
    FirestoreHandRepository,
    TableActionCommitResult,
    TableActionCommitStatus,
)
from logic.connections import ConnectionManager
from logic.first_party_table import FirstPartyNLHGame
from logic.runtime import TableRuntime, TableStateConflict
from logic.table_checkpoint import serialize_first_party_table
from logic.table_ownership import (
    LeaseAlreadyHeldError,
    LeaseExpiredError,
    TableLeaseManager,
    TableRouteManager,
    WorkerCoordinator,
    WorkerCoordinatorRegistry,
    WorkerFleetScheduler,
    WorkerLeaseSession,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_claim_lease_assigns_owner_and_generation() -> None:
    manager = TableLeaseManager(ttl_seconds=30, now_factory=_now)

    lease = manager.claim_lease("table-1", "worker-a")

    assert lease.table_id == "table-1"
    assert lease.owner_worker_id == "worker-a"
    assert lease.fencing_generation == 1
    assert lease.status == "active"


def test_lease_rejects_second_owner_until_expired() -> None:
    manager = TableLeaseManager(ttl_seconds=30, now_factory=_now)
    manager.claim_lease("table-1", "worker-a")

    with pytest.raises(LeaseAlreadyHeldError):
        manager.claim_lease("table-1", "worker-b")


def test_expired_lease_can_be_reclaimed_and_generation_increments() -> None:
    now = _now()
    manager = TableLeaseManager(ttl_seconds=30, now_factory=lambda: now)
    first = manager.claim_lease("table-1", "worker-a")

    now = now + timedelta(seconds=31)
    second = manager.claim_lease("table-1", "worker-b")

    assert first.fencing_generation == 1
    assert second.fencing_generation == 2
    assert second.owner_worker_id == "worker-b"


def test_released_lease_generation_is_not_reused() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)
    first = router.assign_table("table-release", "worker-a")

    assert router.release_table("table-release", "worker-a") is True
    second = router.assign_table("table-release", "worker-b")

    assert first.fencing_generation == 1
    assert second.fencing_generation == 2
    with pytest.raises(LeaseAlreadyHeldError):
        router.authorize_table_action(
            "table-release",
            "worker-a",
            first.fencing_generation,
        )


def test_renew_lease_requires_matching_worker_and_generation() -> None:
    manager = TableLeaseManager(ttl_seconds=30, now_factory=_now)
    lease = manager.claim_lease("table-1", "worker-a")

    with pytest.raises(LeaseAlreadyHeldError):
        manager.renew_lease("table-1", "worker-b", lease.fencing_generation)

    renewed = manager.renew_lease("table-1", "worker-a", lease.fencing_generation)
    assert renewed.lease_expires_at > lease.lease_expires_at


def test_stale_generation_is_rejected_for_actions() -> None:
    now = _now()
    manager = TableLeaseManager(ttl_seconds=30, now_factory=lambda: now)
    first = manager.claim_lease("table-1", "worker-a")

    now = now + timedelta(seconds=31)
    second = manager.claim_lease("table-1", "worker-b")

    assert second.fencing_generation == 2
    with pytest.raises(LeaseAlreadyHeldError):
        manager.authorize_action("table-1", "worker-a", first.fencing_generation)


def test_lease_is_reusable_after_expiry_submission() -> None:
    now = _now()
    manager = TableLeaseManager(ttl_seconds=30, now_factory=lambda: now)
    manager.claim_lease("table-1", "worker-a")

    now = now + timedelta(seconds=31)
    reclaimed = manager.claim_lease("table-1", "worker-b")
    assert reclaimed.owner_worker_id == "worker-b"
    assert reclaimed.fencing_generation == 2

    now = now + timedelta(seconds=31)
    with pytest.raises(LeaseExpiredError):
        manager.authorize_action("table-1", "worker-b", 1)


def test_runtime_enforces_worker_lease_and_generation_fencing() -> None:
    game = FirstPartyNLHGame("runtime-lease-table", 10, 20, 20, 1000)
    runtime = TableRuntime(
        "club-1",
        game,
        ConnectionManager(),
        repository=None,
        turn_timeout_seconds=0,
    )

    first = runtime.claim_lease("worker-a")
    assert first.owner_worker_id == "worker-a"
    assert first.fencing_generation == 1

    with pytest.raises(LeaseAlreadyHeldError):
        runtime.claim_lease("worker-b")

    runtime.claim_lease("worker-b", force_reclaim=True)
    with pytest.raises(LeaseAlreadyHeldError):
        runtime.authorize_action("worker-a", first.fencing_generation)

    active = runtime.authorize_action("worker-b", 2)
    assert active.owner_worker_id == "worker-b"


def test_runtime_uses_durable_lease_repository_for_club_and_union_tables() -> None:
    class LeaseRepository(FirestoreHandRepository):
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []
            self.lease: dict[str, object] | None = None
            self.checkpoints: dict[tuple[str, str, str], dict[str, object]] = {}

        def get_table_checkpoint(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            self.calls.append(("get_checkpoint", scope_type, scope_id, table_id))
            return self.checkpoints.get((scope_type, scope_id, table_id))

        def initialize_table_checkpoint(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            owner_id: str,
            fencing_epoch: int,
            checkpoint: dict[str, object],
        ) -> bool:
            self.calls.append(("initialize_checkpoint", scope_type, scope_id, table_id, fencing_epoch))
            key = (scope_type, scope_id, table_id)
            if key in self.checkpoints:
                return False
            self.checkpoints[key] = {
                "owner_id": owner_id,
                "fencing_epoch": fencing_epoch,
                "sequence": 0,
                "checkpoint": checkpoint,
            }
            return True

        def claim_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            ttl_seconds: int,
            *,
            force_reclaim: bool = False,
            initial_checkpoint: dict[str, object] | None = None,
            now: datetime | None = None,
        ) -> dict[str, object]:
            self.calls.append(("claim", scope_type, scope_id, table_id, worker_id, force_reclaim))
            current = _now()
            self.lease = {
                "table_id": table_id,
                "owner_id": worker_id,
                "fencing_generation": 4,
                "lease_expires_at": current + timedelta(seconds=ttl_seconds),
                "heartbeat_at": current,
                "status": "active",
            }
            key = (scope_type, scope_id, table_id)
            if key not in self.checkpoints and initial_checkpoint is not None:
                self.checkpoints[key] = {
                    "owner_id": worker_id,
                    "fencing_epoch": 4,
                    "sequence": 0,
                    "checkpoint": initial_checkpoint,
                }
            return self.lease

        def get_table_lease(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            self.calls.append(("get", scope_type, scope_id, table_id))
            return self.lease

        def renew_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            fencing_generation: int,
            ttl_seconds: int,
            *,
            now: datetime | None = None,
        ) -> dict[str, object]:
            self.calls.append(("renew", scope_type, scope_id, table_id, worker_id, fencing_generation))
            assert self.lease is not None
            return self.lease

        def authorize_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            fencing_generation: int,
            *,
            now: datetime | None = None,
        ) -> dict[str, object]:
            self.calls.append(("authorize", scope_type, scope_id, table_id, worker_id, fencing_generation))
            assert self.lease is not None
            return self.lease

        def release_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            fencing_generation: int,
            *,
            now: datetime | None = None,
        ) -> bool:
            self.calls.append(("release", scope_type, scope_id, table_id, worker_id, fencing_generation))
            assert self.lease is not None
            self.lease = {**self.lease, "owner_id": None, "status": "released"}
            return True

    repository = LeaseRepository()
    runtime = TableRuntime(
        "club-1",
        FirstPartyNLHGame("durable-club-table", 10, 20, 20, 1000),
        ConnectionManager(),
        repository,
        turn_timeout_seconds=0,
    )

    claimed = runtime.claim_lease("worker-a")
    assert claimed.fencing_generation == 4
    assert runtime.current_lease().owner_worker_id == "worker-a"
    assert runtime.renew_lease("worker-a", 4).fencing_generation == 4
    assert runtime.authorize_action("worker-a", 4).owner_worker_id == "worker-a"
    assert runtime.release_lease("worker-a", 4) is True
    assert runtime.current_lease() is None

    union_runtime = TableRuntime(
        "club-1",
        FirstPartyNLHGame("durable-union-table", 10, 20, 20, 1000),
        ConnectionManager(),
        repository,
        turn_timeout_seconds=0,
        union_id="union-1",
    )
    union_runtime.claim_lease("worker-b")

    assert repository.calls[0] == (
        "claim", "club", "club-1", "durable-club-table", "worker-a", False
    )
    assert ("claim", "union", "union-1", "durable-union-table", "worker-b", False) in repository.calls


def test_runtime_commits_hand_start_checkpoint_under_durable_lease() -> None:
    class DurableRepository(FirestoreHandRepository):
        def __init__(self, commit_status: TableActionCommitStatus) -> None:
            self.commit_status = commit_status
            self.checkpoint: dict[str, object] | None = None
            self.lease: dict[str, object] | None = None
            self.commit_arguments: tuple[object, ...] | None = None

        def claim_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            ttl_seconds: int,
            *,
            force_reclaim: bool = False,
            initial_checkpoint: dict[str, object] | None = None,
            now: datetime | None = None,
        ) -> dict[str, object]:
            current = _now()
            self.lease = {
                "table_id": table_id,
                "owner_id": worker_id,
                "fencing_generation": 1,
                "lease_expires_at": current + timedelta(seconds=ttl_seconds),
                "heartbeat_at": current,
                "status": "active",
            }
            self.checkpoint = {
                "owner_id": worker_id,
                "fencing_epoch": 1,
                "sequence": 0,
                "checkpoint": initial_checkpoint,
            }
            return self.lease

        def get_table_checkpoint(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            return self.checkpoint

        def get_table_lease(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            return self.lease

        def commit_table_action(self, *args: object, **kwargs: object) -> TableActionCommitResult:
            self.commit_arguments = args
            current_sequence = int(self.checkpoint["sequence"])  # type: ignore[index]
            if self.commit_status is TableActionCommitStatus.COMMITTED:
                self.checkpoint = {
                    **self.checkpoint,  # type: ignore[arg-type]
                    "sequence": current_sequence + 1,
                    "checkpoint": args[-1],
                }
                return TableActionCommitResult(self.commit_status, current_sequence + 1)
            return TableActionCommitResult(self.commit_status, current_sequence)

    async def run_test(commit_status: TableActionCommitStatus) -> tuple[TableRuntime, DurableRepository]:
        game = FirstPartyNLHGame("durable-start-table", 10, 20, 20, 1000)
        assert game.add_player(0, "alice", "Alice", 500)
        assert game.add_player(1, "bob", "Bob", 500)
        repository = DurableRepository(commit_status)
        runtime = TableRuntime(
            "club-1", game, ConnectionManager(), repository, turn_timeout_seconds=0
        )
        runtime.claim_lease("worker-a")
        return runtime, repository

    async def verify() -> None:
        runtime, repository = await run_test(TableActionCommitStatus.COMMITTED)
        assert await runtime.start_hand()
        assert repository.commit_arguments is not None
        assert repository.commit_arguments[7] == "start-hand:" + runtime.game._current_hand_id
        assert runtime._table_sequence == 1
        assert repository.checkpoint["checkpoint"] == serialize_first_party_table(runtime.game)

        runtime, repository = await run_test(TableActionCommitStatus.FENCED)
        before = serialize_first_party_table(runtime.game)
        with pytest.raises(TableStateConflict):
            await runtime.start_hand()
        assert serialize_first_party_table(runtime.game) == before

    asyncio.run(verify())


def test_runtime_commits_pre_action_checkpoint_and_restores_when_fenced() -> None:
    class DurableRepository(FirestoreHandRepository):
        def __init__(self, commit_status: TableActionCommitStatus) -> None:
            self.commit_status = commit_status
            self.checkpoint: dict[str, object] | None = None
            self.lease: dict[str, object] | None = None

        def claim_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            ttl_seconds: int,
            *,
            force_reclaim: bool = False,
            initial_checkpoint: dict[str, object] | None = None,
            now: datetime | None = None,
        ) -> dict[str, object]:
            current = _now()
            self.lease = {
                "table_id": table_id,
                "owner_id": worker_id,
                "fencing_generation": 1,
                "lease_expires_at": current + timedelta(seconds=ttl_seconds),
                "heartbeat_at": current,
                "status": "active",
            }
            self.checkpoint = {
                "owner_id": worker_id,
                "fencing_epoch": 1,
                "sequence": 0,
                "checkpoint": initial_checkpoint,
            }
            return self.lease

        def get_table_checkpoint(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            return self.checkpoint

        def get_table_lease(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            return self.lease

        def commit_table_action(self, *args: object, **kwargs: object) -> TableActionCommitResult:
            assert self.checkpoint is not None
            current_sequence = int(self.checkpoint["sequence"])
            if self.commit_status is TableActionCommitStatus.COMMITTED:
                self.checkpoint = {
                    **self.checkpoint,
                    "sequence": current_sequence + 1,
                    "checkpoint": args[-1],
                }
                return TableActionCommitResult(self.commit_status, current_sequence + 1)
            return TableActionCommitResult(self.commit_status, current_sequence)

    async def verify() -> None:
        game = FirstPartyNLHGame("durable-pre-action-table", 10, 20, 20, 1000)
        assert game.add_player(0, "alice", "Alice", 500)
        assert game.add_player(1, "bob", "Bob", 500)
        assert game.start_new_hand()
        waiting_player = "alice" if game.current_actor_user_id == "bob" else "bob"
        repository = DurableRepository(TableActionCommitStatus.COMMITTED)
        runtime = TableRuntime(
            "club-1", game, ConnectionManager(), repository, turn_timeout_seconds=0
        )
        runtime.claim_lease("worker-a")

        assert await runtime.set_pre_action(waiting_player, "CALL_ANY") == "CALL_ANY"
        assert runtime._table_sequence == 1
        persisted = repository.checkpoint["checkpoint"]
        assert persisted == serialize_first_party_table(runtime.game)
        assert runtime.game.get_pre_action_for_player(waiting_player)["action"] == "CALL_ANY"

        repository.commit_status = TableActionCommitStatus.FENCED
        before = serialize_first_party_table(runtime.game)
        with pytest.raises(TableStateConflict):
            await runtime.set_pre_action(waiting_player, "FOLD")
        assert serialize_first_party_table(runtime.game) == before

    asyncio.run(verify())


def test_runtime_commits_timeout_action_checkpoint_under_durable_lease() -> None:
    class DurableRepository(FirestoreHandRepository):
        def __init__(self, commit_status: TableActionCommitStatus) -> None:
            self.commit_status = commit_status
            self.checkpoint: dict[str, object] | None = None
            self.lease: dict[str, object] | None = None
            self.commit_arguments: tuple[object, ...] | None = None

        def claim_table_lease(
            self,
            scope_type: str,
            scope_id: str,
            table_id: str,
            worker_id: str,
            ttl_seconds: int,
            *,
            force_reclaim: bool = False,
            initial_checkpoint: dict[str, object] | None = None,
            now: datetime | None = None,
        ) -> dict[str, object]:
            current = _now()
            self.lease = {
                "table_id": table_id,
                "owner_id": worker_id,
                "fencing_generation": 1,
                "lease_expires_at": current + timedelta(seconds=ttl_seconds),
                "heartbeat_at": current,
                "status": "active",
            }
            self.checkpoint = {
                "owner_id": worker_id,
                "fencing_epoch": 1,
                "sequence": 0,
                "checkpoint": initial_checkpoint,
            }
            return self.lease

        def get_table_checkpoint(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            return self.checkpoint

        def get_table_lease(
            self, scope_type: str, scope_id: str, table_id: str
        ) -> dict[str, object] | None:
            return self.lease

        def commit_table_action(self, *args: object, **kwargs: object) -> TableActionCommitResult:
            assert self.checkpoint is not None
            self.commit_arguments = args
            current_sequence = int(self.checkpoint["sequence"])
            if self.commit_status is TableActionCommitStatus.COMMITTED:
                self.checkpoint = {
                    **self.checkpoint,
                    "sequence": current_sequence + 1,
                    "checkpoint": args[-1],
                }
                return TableActionCommitResult(self.commit_status, current_sequence + 1)
            return TableActionCommitResult(self.commit_status, current_sequence)

        def persist_hand_history_and_ledgers(self, *args: object, **kwargs: object) -> bool:
            return True

    async def make_runtime(
        commit_status: TableActionCommitStatus,
    ) -> tuple[TableRuntime, DurableRepository, str]:
        game = FirstPartyNLHGame("durable-timeout-table", 10, 20, 20, 1000)
        assert game.add_player(0, "alice", "Alice", 500)
        assert game.add_player(1, "bob", "Bob", 500)
        assert game.start_new_hand()
        actor = game.current_actor_user_id
        assert actor is not None
        repository = DurableRepository(commit_status)
        runtime = TableRuntime(
            "club-1", game, ConnectionManager(), repository, turn_timeout_seconds=0
        )
        runtime.claim_lease("worker-a")
        return runtime, repository, actor

    async def verify() -> None:
        runtime, repository, actor = await make_runtime(TableActionCommitStatus.COMMITTED)
        await runtime._timeout_turn(actor)
        assert repository.commit_arguments is not None
        assert repository.commit_arguments[7] == f"timeout:0:{actor}"
        assert runtime._table_sequence == 1
        persisted_checkpoint = repository.checkpoint["checkpoint"]
        assert persisted_checkpoint["runtime_state"]["completed_hand"] is not None

        runtime, repository, actor = await make_runtime(TableActionCommitStatus.FENCED)
        before = serialize_first_party_table(runtime.game)
        with pytest.raises(TableStateConflict):
            await runtime._timeout_turn(actor)
        assert serialize_first_party_table(runtime.game) == before

    asyncio.run(verify())


def test_runtime_state_change_hook_runs_after_hand_start_and_player_action() -> None:
    async def verify() -> None:
        class SettlementRepository:
            def persist_hand_history_and_ledgers(self, *args: object) -> bool:
                return True

        game = FirstPartyNLHGame("broadcast-hook-table", 10, 20, 20, 1000)
        assert game.add_player(0, "alice", "Alice", 500)
        assert game.add_player(1, "bob", "Bob", 500)
        snapshots: list[dict[str, object]] = []

        async def on_state_change(runtime: TableRuntime) -> None:
            snapshots.append(serialize_first_party_table(runtime.game))

        runtime = TableRuntime(
            "club-1",
            game,
            ConnectionManager(),
            repository=SettlementRepository(),
            turn_timeout_seconds=0,
            on_state_change=on_state_change,
        )
        assert await runtime.start_hand()
        actor = runtime.game.current_actor_user_id
        assert actor is not None
        await runtime.act(actor, "FOLD")

        assert len(snapshots) == 2
        assert snapshots[0]["runtime_state"]["is_hand_running"] is True
        assert snapshots[1]["runtime_state"]["is_hand_running"] is False

    asyncio.run(verify())


def test_runtime_recovers_persisted_checkpoint_and_lease() -> None:
    game = FirstPartyNLHGame("recovery-table", 10, 20, 20, 1000)
    game.add_player(0, "alice", "Alice", 500, "buy-in-a", "club-1")
    game.add_player(1, "bob", "Bob", 500, "buy-in-b", "club-1")

    class StubRepository:
        def get_table_checkpoint(self, scope_type: str, scope_id: str, table_id: str):
            assert scope_type == "club"
            assert scope_id == "club-1"
            assert table_id == "recovery-table"
            return {
                "owner_id": "worker-a",
                "fencing_epoch": 7,
                "sequence": 3,
                "checkpoint": serialize_first_party_table(game),
            }

    runtime = TableRuntime(
        "club-1",
        game,
        ConnectionManager(),
        repository=StubRepository(),
        turn_timeout_seconds=0,
    )

    recovered = __import__("asyncio").run(runtime.load_persisted_state())

    assert recovered is True
    assert runtime.game.table_id == "recovery-table"
    assert runtime.game.seats[0].user_id == "alice"
    assert runtime.current_lease() is not None
    assert runtime.current_lease().owner_worker_id == "worker-a"
    assert runtime.current_lease().fencing_generation == 7


def test_route_manager_assigns_unique_workers_across_100_tables() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)

    assignments = {}
    for table_index in range(100):
        worker_id = f"worker-{table_index % 8}"
        lease = router.assign_table(f"table-{table_index}", worker_id)
        assignments[f"table-{table_index}"] = lease

    assert len({lease.owner_worker_id for lease in assignments.values()}) <= 8
    assert all(
        router.current_owner(f"table-{index}") == assignments[f"table-{index}"].owner_worker_id
        for index in range(100)
    )

    stale = router.assign_table("table-0", "worker-99", force_reclaim=True)
    assert stale.owner_worker_id == "worker-99"
    with pytest.raises(LeaseAlreadyHeldError):
        router.authorize_table_action("table-0", "worker-0", 1)


def test_route_manager_recovers_persisted_owner_assignment() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)

    recovered = router.recover_route("table-42", "worker-9", 8)

    assert recovered.owner_worker_id == "worker-9"
    assert recovered.fencing_generation == 8
    assert router.current_owner("table-42") == "worker-9"
    with pytest.raises(LeaseAlreadyHeldError):
        router.assign_table("table-42", "worker-3")


def test_worker_session_claims_and_releases_tables_during_shutdown() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)
    session = WorkerLeaseSession("worker-lease", router)

    session.start()
    first = session.bind_table("table-1")
    second = session.bind_table("table-2")
    assert first.owner_worker_id == "worker-lease"
    assert second.owner_worker_id == "worker-lease"
    assert set(session.assigned_tables()) == {"table-1", "table-2"}

    session.shutdown()
    assert router.current_owner("table-1") is None
    assert router.current_owner("table-2") is None
    assert session.assigned_tables() == []

    reclaimed = router.assign_table("table-1", "worker-other")
    assert reclaimed.owner_worker_id == "worker-other"


def test_worker_coordinator_tracks_lease_heartbeats_and_release() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)
    coordinator = WorkerCoordinator("worker-coord", router)

    coordinator.start()
    lease = coordinator.bind_table("table-9")
    assert lease.owner_worker_id == "worker-coord"
    assert coordinator.current_lease("table-9").owner_worker_id == "worker-coord"

    renewed = coordinator.heartbeat("table-9")
    assert renewed.owner_worker_id == "worker-coord"
    assert renewed.fencing_generation == 1

    coordinator.shutdown()
    assert router.current_owner("table-9") is None
    assert coordinator.assigned_tables() == []


def test_worker_registry_binds_multiple_tables_and_releases_worker_set() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)
    registry = WorkerCoordinatorRegistry(router)

    coordinator = registry.get_or_create("worker-registry")
    coordinator.start()
    leases = registry.bind_tables("worker-registry", ["table-20", "table-21", "table-22"])

    assert set(leases) == {"table-20", "table-21", "table-22"}
    assert set(registry.assigned_tables("worker-registry")) == {"table-20", "table-21", "table-22"}
    assert all(router.current_owner(table_id) == "worker-registry" for table_id in leases)

    released = registry.shutdown_worker("worker-registry")
    assert set(released) == {"table-20", "table-21", "table-22"}
    assert registry.assigned_tables("worker-registry") == []
    assert all(router.current_owner(table_id) is None for table_id in ["table-20", "table-21", "table-22"])


def test_worker_fleet_scheduler_assigns_and_heartbeats_a_table_batch() -> None:
    router = TableRouteManager(ttl_seconds=30, now_factory=_now)
    scheduler = WorkerFleetScheduler(router)

    leases = scheduler.assign_worker_tables("worker-scheduler", ["table-50", "table-51", "table-52"])
    assert set(leases) == {"table-50", "table-51", "table-52"}
    assert all(router.current_owner(table_id) == "worker-scheduler" for table_id in leases)

    refreshed = scheduler.heartbeat_worker("worker-scheduler", ["table-50", "table-51"])
    assert set(refreshed) == {"table-50", "table-51"}
    assert all(refreshed[table_id].owner_worker_id == "worker-scheduler" for table_id in refreshed)

    released = scheduler.release_worker_tables("worker-scheduler", ["table-50", "table-52"])
    assert set(released) == {"table-50", "table-52"}
    assert router.current_owner("table-50") is None
    assert router.current_owner("table-52") is None
    assert set(scheduler.assigned_tables("worker-scheduler")) == {"table-51"}
