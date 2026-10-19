from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from logic.connections import ConnectionManager
from logic.first_party_table import FirstPartyNLHGame
from logic.runtime import TableRuntime
from logic.table_checkpoint import serialize_first_party_table
from logic.table_ownership import (
    LeaseAlreadyHeldError,
    LeaseExpiredError,
    TableLeaseManager,
    TableRouteManager,
    WorkerCoordinator,
    WorkerCoordinatorRegistry,
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


def test_runtime_recovers_persisted_checkpoint_without_reusing_old_lease() -> None:
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
    assert runtime.current_lease() is None


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
