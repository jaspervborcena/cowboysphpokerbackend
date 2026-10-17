from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable


class LeaseAlreadyHeldError(RuntimeError):
    pass


class LeaseExpiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class TableLease:
    table_id: str
    owner_worker_id: str
    fencing_generation: int
    lease_expires_at: datetime
    heartbeat_at: datetime
    state_version: int = 0
    last_action_seq: int = 0
    status: str = "active"


class TableLeaseManager:
    def __init__(self, ttl_seconds: int = 30, now_factory: Callable[[], datetime] | None = None) -> None:
        self.ttl_seconds = ttl_seconds
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._leases: dict[str, TableLease] = {}

    def claim_lease(
        self,
        table_id: str,
        worker_id: str,
        *,
        force_reclaim: bool = False,
    ) -> TableLease:
        now = self._now_factory()
        current = self._leases.get(table_id)

        if current is None:
            lease = self._new_lease(table_id, worker_id, generation=1, now=now)
            self._leases[table_id] = lease
            return lease

        if current.owner_worker_id == worker_id and current.lease_expires_at > now:
            return current

        if current.lease_expires_at > now and not force_reclaim:
            raise LeaseAlreadyHeldError(
                f"Table {table_id} is already owned by worker {current.owner_worker_id}."
            )

        new_generation = current.fencing_generation + 1
        lease = self._new_lease(table_id, worker_id, generation=new_generation, now=now)
        self._leases[table_id] = lease
        return lease

    def recover_lease(
        self,
        table_id: str,
        owner_worker_id: str,
        fencing_generation: int,
    ) -> TableLease:
        if fencing_generation < 1:
            raise ValueError("Fencing generation must be at least 1.")
        now = self._now_factory()
        lease = self._new_lease(
            table_id,
            owner_worker_id,
            generation=fencing_generation,
            now=now,
        )
        self._leases[table_id] = lease
        return lease

    def renew_lease(self, table_id: str, worker_id: str, fencing_generation: int) -> TableLease:
        now = self._now_factory()
        current = self._leases.get(table_id)
        if current is None:
            raise LeaseExpiredError(f"No active lease exists for table {table_id}.")
        if current.owner_worker_id != worker_id:
            raise LeaseAlreadyHeldError(
                f"Table {table_id} is owned by worker {current.owner_worker_id}, not {worker_id}."
            )
        if current.fencing_generation != fencing_generation:
            raise LeaseExpiredError(
                f"Lease generation mismatch for table {table_id}. Expected {current.fencing_generation}, got {fencing_generation}."
            )
        if current.lease_expires_at <= now:
            raise LeaseExpiredError(f"Lease for table {table_id} has expired.")

        renewed = replace(
            current,
            lease_expires_at=now + timedelta(seconds=self.ttl_seconds),
            heartbeat_at=now,
        )
        self._leases[table_id] = renewed
        return renewed

    def authorize_action(self, table_id: str, worker_id: str, fencing_generation: int) -> TableLease:
        now = self._now_factory()
        current = self._leases.get(table_id)
        if current is None:
            raise LeaseExpiredError(f"No active lease exists for table {table_id}.")
        if current.owner_worker_id != worker_id:
            raise LeaseAlreadyHeldError(
                f"Table {table_id} is not owned by worker {worker_id}."
            )
        if current.fencing_generation != fencing_generation:
            raise LeaseExpiredError(
                f"Action generation {fencing_generation} is stale for table {table_id}."
            )
        if current.lease_expires_at <= now:
            raise LeaseExpiredError(f"Lease for table {table_id} has expired.")
        return current

    def _new_lease(
        self,
        table_id: str,
        worker_id: str,
        *,
        generation: int,
        now: datetime,
    ) -> TableLease:
        expires = now + timedelta(seconds=self.ttl_seconds)
        return TableLease(
            table_id=table_id,
            owner_worker_id=worker_id,
            fencing_generation=generation,
            lease_expires_at=expires,
            heartbeat_at=now,
            state_version=0,
            last_action_seq=0,
            status="active",
        )

    def assign_table(
        self,
        table_id: str,
        worker_id: str,
        *,
        force_reclaim: bool = False,
    ) -> TableLease:
        return self.claim_lease(table_id, worker_id, force_reclaim=force_reclaim)

    def current_owner(self, table_id: str) -> str | None:
        lease = self._leases.get(table_id)
        if lease is None:
            return None
        return lease.owner_worker_id

    def current_table_lease(self, table_id: str) -> TableLease | None:
        return self._leases.get(table_id)

    def authorize_table_action(
        self,
        table_id: str,
        worker_id: str,
        fencing_generation: int,
    ) -> TableLease:
        return self.authorize_action(table_id, worker_id, fencing_generation)

    def renew_table(self, table_id: str, worker_id: str, fencing_generation: int) -> TableLease:
        return self.renew_lease(table_id, worker_id, fencing_generation)

    def recover_route(
        self,
        table_id: str,
        owner_worker_id: str,
        fencing_generation: int,
    ) -> TableLease:
        return self.recover_lease(table_id, owner_worker_id, fencing_generation)

    def release_table(self, table_id: str, worker_id: str) -> bool:
        lease = self._leases.get(table_id)
        if lease is None:
            return False
        if lease.owner_worker_id != worker_id:
            raise LeaseAlreadyHeldError(
                f"Table {table_id} is not owned by worker {worker_id}."
            )
        if lease.lease_expires_at <= self._now_factory():
            raise LeaseExpiredError(f"Lease for table {table_id} has expired.")
        del self._leases[table_id]
        return True


class WorkerLeaseSession:
    def __init__(self, worker_id: str, route_manager: TableRouteManager) -> None:
        self.worker_id = worker_id
        self.route_manager = route_manager
        self._assigned_tables: set[str] = set()
        self._active = False

    def start(self) -> "WorkerLeaseSession":
        self._active = True
        return self

    def bind_table(self, table_id: str, *, force_reclaim: bool = False) -> TableLease:
        if not self._active:
            raise RuntimeError("Worker session must be started before claiming tables.")
        lease = self.route_manager.assign_table(
            table_id,
            self.worker_id,
            force_reclaim=force_reclaim,
        )
        self._assigned_tables.add(table_id)
        return lease

    def renew_table(self, table_id: str) -> TableLease:
        lease = self.route_manager.current_table_lease(table_id)
        if lease is None:
            raise LeaseExpiredError(f"No active route exists for table {table_id}.")
        if lease.owner_worker_id != self.worker_id:
            raise LeaseAlreadyHeldError(
                f"Table {table_id} is not owned by worker {self.worker_id}."
            )
        return self.route_manager.renew_table(table_id, self.worker_id, lease.fencing_generation)

    def assigned_tables(self) -> list[str]:
        return sorted(self._assigned_tables)

    def release_table(self, table_id: str) -> bool:
        if table_id not in self._assigned_tables:
            return False
        released = self.route_manager.release_table(table_id, self.worker_id)
        if released:
            self._assigned_tables.discard(table_id)
        return released

    def shutdown(self) -> list[str]:
        released = []
        for table_id in sorted(self._assigned_tables):
            if self.release_table(table_id):
                released.append(table_id)
        self._active = False
        return released

    def current_lease(self, table_id: str) -> TableLease | None:
        current = self.route_manager.current_table_lease(table_id)
        if current is None or current.owner_worker_id != self.worker_id:
            return None
        return current


class WorkerCoordinator:
    def __init__(self, worker_id: str, route_manager: TableRouteManager) -> None:
        self.worker_id = worker_id
        self.route_manager = route_manager
        self.session = WorkerLeaseSession(worker_id, route_manager)

    def start(self) -> "WorkerCoordinator":
        self.session.start()
        return self

    def bind_table(self, table_id: str, *, force_reclaim: bool = False) -> TableLease:
        return self.session.bind_table(table_id, force_reclaim=force_reclaim)

    def heartbeat(self, table_id: str) -> TableLease:
        return self.session.renew_table(table_id)

    def current_lease(self, table_id: str) -> TableLease | None:
        return self.session.current_lease(table_id)

    def assigned_tables(self) -> list[str]:
        return self.session.assigned_tables()

    def shutdown(self) -> list[str]:
        return self.session.shutdown()


class WorkerCoordinatorRegistry:
    def __init__(self, route_manager: TableRouteManager) -> None:
        self.route_manager = route_manager
        self._coordinators: dict[str, WorkerCoordinator] = {}

    def get_or_create(self, worker_id: str) -> WorkerCoordinator:
        coordinator = self._coordinators.get(worker_id)
        if coordinator is None:
            coordinator = WorkerCoordinator(worker_id, self.route_manager)
            self._coordinators[worker_id] = coordinator
        return coordinator

    def start_worker(self, worker_id: str) -> WorkerCoordinator:
        coordinator = self.get_or_create(worker_id)
        coordinator.start()
        return coordinator

    def bind_tables(
        self,
        worker_id: str,
        table_ids: list[str],
        *,
        force_reclaim: bool = False,
    ) -> dict[str, TableLease]:
        coordinator = self.start_worker(worker_id)
        leases: dict[str, TableLease] = {}
        for table_id in table_ids:
            leases[table_id] = coordinator.bind_table(table_id, force_reclaim=force_reclaim)
        return leases

    def assigned_tables(self, worker_id: str) -> list[str]:
        coordinator = self._coordinators.get(worker_id)
        if coordinator is None:
            return []
        return coordinator.assigned_tables()

    def shutdown_worker(self, worker_id: str) -> list[str]:
        coordinator = self._coordinators.get(worker_id)
        if coordinator is None:
            return []
        released = coordinator.shutdown()
        self._coordinators.pop(worker_id, None)
        return released


class TableRouteManager:
    def __init__(self, ttl_seconds: int = 30, now_factory: Callable[[], datetime] | None = None) -> None:
        self._lease_manager = TableLeaseManager(ttl_seconds=ttl_seconds, now_factory=now_factory)

    def assign_table(
        self,
        table_id: str,
        worker_id: str,
        *,
        force_reclaim: bool = False,
    ) -> TableLease:
        return self._lease_manager.claim_lease(
            table_id,
            worker_id,
            force_reclaim=force_reclaim,
        )

    def current_owner(self, table_id: str) -> str | None:
        lease = self._lease_manager._leases.get(table_id)
        if lease is None:
            return None
        return lease.owner_worker_id

    def current_table_lease(self, table_id: str) -> TableLease | None:
        return self._lease_manager._leases.get(table_id)

    def authorize_table_action(
        self,
        table_id: str,
        worker_id: str,
        fencing_generation: int,
    ) -> TableLease:
        return self._lease_manager.authorize_action(
            table_id,
            worker_id,
            fencing_generation,
        )

    def renew_table(self, table_id: str, worker_id: str, fencing_generation: int) -> TableLease:
        return self._lease_manager.renew_lease(
            table_id,
            worker_id,
            fencing_generation,
        )

    def recover_route(
        self,
        table_id: str,
        owner_worker_id: str,
        fencing_generation: int,
    ) -> TableLease:
        return self._lease_manager.recover_lease(
            table_id,
            owner_worker_id,
            fencing_generation,
        )

    def release_table(self, table_id: str, worker_id: str) -> bool:
        lease = self._lease_manager._leases.get(table_id)
        if lease is None:
            return False
        if lease.owner_worker_id != worker_id:
            raise LeaseAlreadyHeldError(
                f"Table {table_id} is not owned by worker {worker_id}."
            )
        if lease.lease_expires_at <= self._lease_manager._now_factory():
            raise LeaseExpiredError(f"Lease for table {table_id} has expired.")
        del self._lease_manager._leases[table_id]
        return True
