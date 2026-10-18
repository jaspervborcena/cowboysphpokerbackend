from __future__ import annotations

import hashlib
import json
import time
from enum import StrEnum
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from google.cloud import firestore


INITIAL_CLUB_TREASURY_FUNDS = 10_000
INITIAL_GLOBAL_PLAYMONEY_FUNDS = 20_000
ID_ALLOCATION_ATTEMPTS = 128


def epoch_8_digit_id(offset: int = 0) -> str:
    epoch_seconds = int(time.time())
    return f"{10_000_000 + ((epoch_seconds + offset) % 90_000_000):08d}"


class BuyInStatus(StrEnum):
    RESERVED = "reserved"
    ALREADY_RESERVED = "already_reserved"
    MEMBER_NOT_FOUND = "member_not_found"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    INVALID_BALANCE = "invalid_balance"
    SEAT_OCCUPIED = "seat_occupied"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNION_CLUB_NOT_FOUND = "union_club_not_found"
    TABLE_STATE_CONFLICT = "table_state_conflict"


class WalletTransactionStatus(StrEnum):
    COMPLETED = "completed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    CLUB_NOT_FOUND = "club_not_found"
    SAME_CLUB = "same_club"
    UNAUTHORIZED_OPERATOR = "unauthorized_operator"
    INVALID_AMOUNT = "invalid_amount"
    MEMBER_NOT_FOUND = "member_not_found"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    PLAYER_HAS_CHIPS_IN_PLAY = "player_has_chips_in_play"
    UNAUTHORIZED_DOWNLINE = "unauthorized_downline"
    INVALID_BALANCE = "invalid_balance"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


class TableActionCommitStatus(StrEnum):
    COMMITTED = "committed"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    NOT_INITIALIZED = "not_initialized"
    FENCED = "fenced"
    SEQUENCE_CONFLICT = "sequence_conflict"


class UnionTableCloseStatus(StrEnum):
    CLOSED = "closed"
    ALREADY_CLOSED = "already_closed"
    NOT_FOUND = "not_found"
    CHECKPOINT_MISSING = "checkpoint_missing"
    NOT_EMPTY = "not_empty"
    ACTIVE_LEASE = "active_lease"


class UnionRequestStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    NOT_FOUND = "not_found"
    NOT_AUTHORIZED = "not_authorized"


@dataclass(frozen=True)
class TableActionCommitResult:
    status: TableActionCommitStatus
    sequence: int


def table_action_fingerprint(
    user_id: str,
    action_id: str,
    action_payload: dict[str, Any],
) -> str:
    canonical = json.dumps(
        {"user_id": user_id, "action_id": action_id, "payload": action_payload},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FirestoreRequiredError(RuntimeError):
    pass


class TableLeaseConflictError(RuntimeError):
    pass


class TableLeaseExpiredError(RuntimeError):
    pass


class NullHandRepository:
    def recover_pending_settlements(self) -> int:
        return 0

    def start_worker_session(self, worker_id: str, ttl_seconds: int) -> dict[str, Any]:
        raise FirestoreRequiredError("Firestore must be enabled for durable worker sessions.")

    def heartbeat_worker_session(self, worker_id: str, ttl_seconds: int) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled for durable worker sessions.")

    def worker_session_is_active(self, worker_id: str) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled for durable worker sessions.")

    def stop_worker_session(self, worker_id: str) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled for durable worker sessions.")

    def list_worker_table_leases(self, worker_id: str) -> list[dict[str, Any]]:
        raise FirestoreRequiredError("Firestore must be enabled for durable worker sessions.")

    def get_table_checkpoint(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
    ) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load a table checkpoint.")

    def get_table_lease(
        self, scope_type: str, scope_id: str, table_id: str
    ) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load a table lease.")

    def claim_table_lease(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        worker_id: str,
        ttl_seconds: int,
        *,
        force_reclaim: bool = False,
        initial_checkpoint: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        raise FirestoreRequiredError("Firestore must be enabled to claim a table lease.")

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
    ) -> dict[str, Any]:
        raise FirestoreRequiredError("Firestore must be enabled to renew a table lease.")

    def authorize_table_lease(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        worker_id: str,
        fencing_generation: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        raise FirestoreRequiredError("Firestore must be enabled to authorize a table lease.")

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
        raise FirestoreRequiredError("Firestore must be enabled to release a table lease.")

    def initialize_table_checkpoint(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        owner_id: str | None,
        fencing_epoch: int,
        checkpoint: dict[str, Any],
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to initialize a table checkpoint.")

    def commit_table_action(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        owner_id: str,
        fencing_epoch: int,
        expected_sequence: int,
        user_id: str,
        action_id: str,
        payload_hash: str,
        checkpoint: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> TableActionCommitResult:
        raise FirestoreRequiredError("Firestore must be enabled to persist a table action.")

    def ensure_global_wallet(self, user_id: str) -> tuple[dict[str, Any], bool]:
        raise FirestoreRequiredError("Firestore must be enabled to provision a global wallet.")

    def ensure_user_profile(
        self, user_id: str, username: str
    ) -> tuple[dict[str, Any], bool]:
        raise FirestoreRequiredError("Firestore must be enabled to provision a user profile.")

    def get_user_profile_by_player_id(self, player_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to resolve a player ID.")

    def get_global_wallet(self, user_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load a global wallet.")

    def get_club_wallet_balance(self, club_id: str) -> int:
        raise FirestoreRequiredError("Firestore must be enabled to load the club wallet.")

    def create_club(
        self,
        club_uuid: str,
        club_id: str,
        name: str,
        owner_user_id: str,
        created_by: str,
    ) -> tuple[bool, str]:
        raise FirestoreRequiredError("Firestore must be enabled to create a club.")

    def create_agent_group(
        self,
        club_uuid: str,
        club_id: str,
        agent_group_id: str,
        agent_player_id: str,
        created_by: str,
    ) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to create an agent group.")

    def get_club_profile(self, club_uuid: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load a club profile.")

    def get_agent_group(self, agent_group_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load an agent group.")

    def add_agent_group_member(
        self,
        club_uuid: str,
        club_id: str,
        agent_group_id: str,
        member_player_id: str,
        assigned_by: str,
    ) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to assign an agent-group member.")

    def list_agent_group_members(self, agent_group_id: str) -> list[dict[str, Any]]:
        raise FirestoreRequiredError("Firestore must be enabled to list agent-group members.")

    def transfer_between_club_wallets(
        self,
        source_club_id: str,
        destination_club_id: str,
        operator_user_id: str,
        amount: int,
        transaction_id: str,
        performed_by: str,
        note: str = "",
    ) -> WalletTransactionStatus:
        raise FirestoreRequiredError("Firestore must be enabled for club wallet transfers.")

    def send_out_club_chips(
        self,
        club_id: str,
        sender_user_id: str,
        recipient_user_id: str,
        amount: int,
        transaction_id: str,
        performed_by: str,
        note: str = "",
    ) -> WalletTransactionStatus:
        raise FirestoreRequiredError("Firestore must be enabled for club wallet transfers.")

    def claim_back_club_chips(
        self,
        club_id: str,
        player_user_id: str,
        recipient_user_id: str,
        amount: int,
        transaction_id: str,
        performed_by: str,
        note: str = "",
    ) -> WalletTransactionStatus:
        raise FirestoreRequiredError("Firestore must be enabled for club wallet transfers.")

    def get_club_member(self, club_id: str, user_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to verify club membership.")

    def list_club_player_rake_stats(
        self, club_id: str, agent_user_id: str | None = None
    ) -> list[dict[str, Any]]:
        raise FirestoreRequiredError("Firestore must be enabled to load player rake statistics.")

    def get_hand_history(self, club_id: str, hand_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load hand history.")

    def get_union_hand_history(self, union_id: str, hand_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load union hand history.")

    def get_union_admin(self, union_id: str, user_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to verify union membership.")

    def get_union_profile(self, union_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load a union profile.")

    def get_union_by_code(self, union_code: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to resolve a union code.")

    def create_union(
        self,
        union_id: str,
        union_data: dict[str, Any],
        union_code: str,
        creator_user_id: str,
        founding_club_id: str,
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to create a union.")

    def get_union_representative(
        self, union_id: str, user_id: str
    ) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to verify union representatives.")

    def request_union_representative(
        self, union_id: str, user_id: str, club_id: str, club_role: str
    ) -> UnionRequestStatus:
        raise FirestoreRequiredError("Firestore must be enabled to request union representation.")

    def list_union_representative_requests(self, union_id: str) -> list[dict[str, Any]]:
        raise FirestoreRequiredError("Firestore must be enabled to list union representative requests.")

    def approve_union_representative(
        self, union_id: str, user_id: str, approved_by: str
    ) -> UnionRequestStatus:
        raise FirestoreRequiredError("Firestore must be enabled to approve union representatives.")

    def request_union_club(
        self,
        union_id: str,
        club_id: str,
        union_name: str,
        union_code: str,
        requested_by: str,
    ) -> UnionRequestStatus:
        raise FirestoreRequiredError("Firestore must be enabled to request union club membership.")

    def list_union_club_requests(self, union_id: str) -> list[dict[str, Any]]:
        raise FirestoreRequiredError("Firestore must be enabled to list union club requests.")

    def approve_union_club(
        self, union_id: str, club_id: str, approved_by: str
    ) -> UnionRequestStatus:
        raise FirestoreRequiredError("Firestore must be enabled to approve union clubs.")

    def is_club_in_union(self, union_id: str, club_id: str) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to verify union club membership.")

    def get_unions_for_club(self, club_id: str) -> list[dict[str, str]]:
        raise FirestoreRequiredError("Firestore must be enabled to load club unions.")

    def set_union_admin(
        self,
        union_id: str,
        user_id: str,
        role: str,
        status: str,
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to provision a union administrator.")

    def add_union_club(
        self,
        union_id: str,
        club_id: str,
        club_data: dict[str, Any],
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to provision a union club membership.")

    def create_union_table_record(
        self,
        union_id: str,
        table_id: str,
        table_data: dict[str, Any],
        *,
        runtime_checkpoint: dict[str, Any] | None = None,
        required_active_club_id: str | None = None,
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to create a union table.")

    def close_union_table_record(
        self, union_id: str, table_id: str
    ) -> UnionTableCloseStatus:
        raise FirestoreRequiredError("Firestore must be enabled to close a union table.")

    def has_union_table_seats(self, union_id: str, table_id: str) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to check union table seats.")

    def reserve_union_buy_in(
        self,
        union_id: str,
        table_id: str,
        club_id: str,
        seat_index: int,
        user_id: str,
        username: str,
        buy_in_amount: int,
        buy_in_id: str,
        *,
        runtime_checkpoint: dict[str, Any] | None = None,
        owner_id: str | None = None,
        fencing_epoch: int | None = None,
        expected_sequence: int | None = None,
    ) -> BuyInStatus:
        raise FirestoreRequiredError("Firestore must be enabled to reserve a union table seat.")

    def persist_union_hand_history_and_ledgers(
        self,
        union_id: str,
        table_id: str,
        hand_summary: dict[str, Any],
        chip_deltas: dict[str, int],
        player_clubs: dict[str, str],
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to settle a union hand.")

    def persist_hand_history_and_ledgers(
        self,
        club_id: str,
        table_id: str,
        hand_summary: dict[str, Any],
        chip_deltas: dict[str, int],
    ) -> None:
        return None

    def reserve_buy_in(
        self,
        club_id: str,
        table_id: str,
        seat_index: int,
        user_id: str,
        username: str,
        buy_in_amount: int,
        buy_in_id: str,
        *,
        runtime_checkpoint: dict[str, Any] | None = None,
        owner_id: str | None = None,
        fencing_epoch: int | None = None,
        expected_sequence: int | None = None,
    ) -> BuyInStatus:
        raise FirestoreRequiredError("Firestore must be enabled to reserve a seat.")


class FirestoreHandRepository:
    def __init__(self, client: firestore.Client) -> None:
        self._client = client

    def start_worker_session(self, worker_id: str, ttl_seconds: int) -> dict[str, Any]:
        if not worker_id or ttl_seconds < 1:
            raise ValueError("Worker ID and a positive session TTL are required.")
        current_time = datetime.now(timezone.utc)
        session_ref = self._client.collection("worker_sessions").document(worker_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def start(transaction: Any) -> dict[str, Any]:
            current_snapshot = session_ref.get(transaction=transaction)
            current_session = current_snapshot.to_dict() or {} if current_snapshot.exists else {}
            expires_at = current_session.get("expires_at")
            if (
                current_session.get("status") == "active"
                and expires_at is not None
                and expires_at > current_time
            ):
                return {
                    **current_session,
                    "already_active": True,
                }
            session = {
                "worker_id": worker_id,
                "status": "active",
                "already_active": False,
                "started_at": current_time,
                "heartbeat_at": current_time,
                "expires_at": current_time + timedelta(seconds=ttl_seconds),
            }
            transaction.set(session_ref, session)
            return session

        return start(transaction)

    def heartbeat_worker_session(self, worker_id: str, ttl_seconds: int) -> bool:
        if not worker_id or ttl_seconds < 1:
            raise ValueError("Worker ID and a positive session TTL are required.")
        current_time = datetime.now(timezone.utc)
        session_ref = self._client.collection("worker_sessions").document(worker_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def heartbeat(transaction: Any) -> bool:
            snapshot = session_ref.get(transaction=transaction)
            session = snapshot.to_dict() or {} if snapshot.exists else {}
            if (
                session.get("status") != "active"
                or session.get("expires_at") is None
                or session["expires_at"] <= current_time
            ):
                return False
            transaction.set(
                session_ref,
                {
                    **session,
                    "heartbeat_at": current_time,
                    "expires_at": current_time + timedelta(seconds=ttl_seconds),
                },
            )
            return True

        return heartbeat(transaction)

    def worker_session_is_active(self, worker_id: str) -> bool:
        snapshot = self._client.collection("worker_sessions").document(worker_id).get()
        session = snapshot.to_dict() or {} if snapshot.exists else {}
        expires_at = session.get("expires_at")
        return (
            session.get("status") == "active"
            and expires_at is not None
            and expires_at > datetime.now(timezone.utc)
        )

    def stop_worker_session(self, worker_id: str) -> bool:
        current_time = datetime.now(timezone.utc)
        session_ref = self._client.collection("worker_sessions").document(worker_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def stop(transaction: Any) -> bool:
            snapshot = session_ref.get(transaction=transaction)
            if not snapshot.exists:
                return False
            session = snapshot.to_dict() or {}
            transaction.set(
                session_ref,
                {
                    **session,
                    "status": "stopped",
                    "stopped_at": current_time,
                    "expires_at": current_time,
                },
            )
            return True

        return stop(transaction)

    def list_worker_table_leases(self, worker_id: str) -> list[dict[str, Any]]:
        current_time = datetime.now(timezone.utc)
        snapshots = self._client.collection_group("runtime").where(
            "owner_id", "==", worker_id
        ).stream()
        leases = []
        for snapshot in snapshots:
            record = snapshot.to_dict() or {}
            expires_at = record.get("lease_expires_at")
            if (
                record.get("fencing_generation") is not None
                and expires_at is not None
                and expires_at > current_time
            ):
                leases.append(record)
        return sorted(
            leases,
            key=lambda record: (
                str(record.get("scope_type", "")),
                str(record.get("scope_id", "")),
                str(record.get("table_id", "")),
            ),
        )

    def get_table_checkpoint(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
    ) -> dict[str, Any] | None:
        state_ref = self._table_runtime_state_ref(scope_type, scope_id, table_id)
        snapshot = state_ref.get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict() or {}

    def get_table_lease(
        self, scope_type: str, scope_id: str, table_id: str
    ) -> dict[str, Any] | None:
        snapshot = self._table_runtime_lease_ref(scope_type, scope_id, table_id).get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict() or {}

    def claim_table_lease(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        worker_id: str,
        ttl_seconds: int,
        *,
        force_reclaim: bool = False,
        initial_checkpoint: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not worker_id or ttl_seconds < 1:
            raise ValueError("Worker ID and a positive lease TTL are required.")
        current_time = now or datetime.now(timezone.utc)
        lease_ref = self._table_runtime_lease_ref(scope_type, scope_id, table_id)
        state_ref = self._table_runtime_state_ref(scope_type, scope_id, table_id)
        table_ref = (
            self._client.collection("unions")
            .document(scope_id)
            .collection("tables")
            .document(table_id)
            if scope_type == "union"
            else None
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def claim(transaction: Any) -> dict[str, Any]:
            if table_ref is not None:
                table_snapshot = table_ref.get(transaction=transaction)
                table_data = table_snapshot.to_dict() or {} if table_snapshot.exists else {}
                if not table_snapshot.exists or table_data.get("status") == "closed":
                    raise TableLeaseConflictError("Closed or missing union tables cannot be claimed.")
            lease_snapshot = lease_ref.get(transaction=transaction)
            state_snapshot = state_ref.get(transaction=transaction)
            current = lease_snapshot.to_dict() or {} if lease_snapshot.exists else {}
            owner_id = current.get("owner_id")
            expires_at = current.get("lease_expires_at")
            active = owner_id is not None and expires_at is not None and expires_at > current_time
            if active and owner_id == worker_id:
                if not state_snapshot.exists and initial_checkpoint is not None:
                    generation = int(current["fencing_generation"])
                    _validate_table_checkpoint_write(
                        scope_type,
                        scope_id,
                        table_id,
                        worker_id,
                        generation,
                        0,
                        initial_checkpoint,
                    )
                    transaction.set(
                        state_ref,
                        {
                            "schema_version": 1,
                            "scope_type": scope_type,
                            "scope_id": scope_id,
                            "table_id": table_id,
                            "owner_id": worker_id,
                            "fencing_epoch": generation,
                            "sequence": 0,
                            "checkpoint": initial_checkpoint,
                            "updated_at": firestore.SERVER_TIMESTAMP,
                        },
                    )
                return current
            if active and not force_reclaim:
                raise TableLeaseConflictError(
                    f"Table {table_id} is already owned by worker {owner_id}."
                )

            state = state_snapshot.to_dict() or {} if state_snapshot.exists else {}
            previous_generation = max(
                int(current.get("fencing_generation", 0)),
                int(state.get("fencing_epoch", 0)),
            )
            generation = previous_generation + 1
            if not state_snapshot.exists and initial_checkpoint is not None:
                _validate_table_checkpoint_write(
                    scope_type,
                    scope_id,
                    table_id,
                    worker_id,
                    generation,
                    0,
                    initial_checkpoint,
                )
            lease = {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "table_id": table_id,
                "owner_id": worker_id,
                "fencing_generation": generation,
                "lease_expires_at": current_time + timedelta(seconds=ttl_seconds),
                "heartbeat_at": current_time,
                "status": "active",
            }
            transaction.set(lease_ref, lease)
            if state_snapshot.exists:
                transaction.set(
                    state_ref,
                    {**state, "owner_id": worker_id, "fencing_epoch": generation},
                )
            elif initial_checkpoint is not None:
                transaction.set(
                    state_ref,
                    {
                        "schema_version": 1,
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                        "table_id": table_id,
                        "owner_id": worker_id,
                        "fencing_epoch": generation,
                        "sequence": 0,
                        "checkpoint": initial_checkpoint,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            return lease

        return claim(transaction)

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
    ) -> dict[str, Any]:
        current_time = now or datetime.now(timezone.utc)
        lease_ref = self._table_runtime_lease_ref(scope_type, scope_id, table_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def renew(transaction: Any) -> dict[str, Any]:
            snapshot = lease_ref.get(transaction=transaction)
            lease = snapshot.to_dict() or {} if snapshot.exists else {}
            if lease.get("owner_id") != worker_id:
                raise TableLeaseConflictError(
                    f"Table {table_id} is not owned by worker {worker_id}."
                )
            if lease.get("fencing_generation") != fencing_generation:
                raise TableLeaseExpiredError(
                    f"Lease generation mismatch for table {table_id}."
                )
            if lease.get("lease_expires_at") is None or lease["lease_expires_at"] <= current_time:
                raise TableLeaseExpiredError(f"Lease for table {table_id} has expired.")
            renewed = {
                **lease,
                "lease_expires_at": current_time + timedelta(seconds=ttl_seconds),
                "heartbeat_at": current_time,
            }
            transaction.set(lease_ref, renewed)
            return renewed

        return renew(transaction)

    def authorize_table_lease(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        worker_id: str,
        fencing_generation: int,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current_time = now or datetime.now(timezone.utc)
        lease = self.get_table_lease(scope_type, scope_id, table_id)
        if lease is None:
            raise TableLeaseExpiredError(f"No active lease exists for table {table_id}.")
        if lease.get("owner_id") != worker_id:
            raise TableLeaseConflictError(
                f"Table {table_id} is not owned by worker {worker_id}."
            )
        if lease.get("fencing_generation") != fencing_generation:
            raise TableLeaseExpiredError(f"Lease generation mismatch for table {table_id}.")
        if lease.get("lease_expires_at") is None or lease["lease_expires_at"] <= current_time:
            raise TableLeaseExpiredError(f"Lease for table {table_id} has expired.")
        return lease

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
        current_time = now or datetime.now(timezone.utc)
        lease_ref = self._table_runtime_lease_ref(scope_type, scope_id, table_id)
        state_ref = self._table_runtime_state_ref(scope_type, scope_id, table_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def release(transaction: Any) -> bool:
            lease_snapshot = lease_ref.get(transaction=transaction)
            state_snapshot = state_ref.get(transaction=transaction)
            lease = lease_snapshot.to_dict() or {} if lease_snapshot.exists else {}
            if not lease_snapshot.exists or lease.get("owner_id") is None:
                return False
            if lease.get("owner_id") != worker_id:
                raise TableLeaseConflictError(
                    f"Table {table_id} is not owned by worker {worker_id}."
                )
            if lease.get("fencing_generation") != fencing_generation:
                raise TableLeaseExpiredError(f"Lease generation mismatch for table {table_id}.")
            if lease.get("lease_expires_at") is None or lease["lease_expires_at"] <= current_time:
                raise TableLeaseExpiredError(f"Lease for table {table_id} has expired.")
            transaction.set(
                lease_ref,
                {
                    **lease,
                    "owner_id": None,
                    "lease_expires_at": current_time,
                    "heartbeat_at": current_time,
                    "status": "released",
                },
            )
            if state_snapshot.exists:
                state = state_snapshot.to_dict() or {}
                transaction.set(state_ref, {**state, "owner_id": None})
            return True

        return release(transaction)

    def initialize_table_checkpoint(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        owner_id: str | None,
        fencing_epoch: int,
        checkpoint: dict[str, Any],
    ) -> bool:
        if owner_id is None:
            if fencing_epoch != 0:
                raise ValueError("Unowned table checkpoints must start at fencing epoch zero.")
            _validate_unowned_table_checkpoint(scope_type, scope_id, table_id, checkpoint)
        else:
            _validate_table_checkpoint_write(
                scope_type, scope_id, table_id, owner_id, fencing_epoch, 0, checkpoint
            )
        state_ref = self._table_runtime_state_ref(scope_type, scope_id, table_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            if state_ref.get(transaction=transaction).exists:
                return False
            transaction.set(
                state_ref,
                {
                    "schema_version": 1,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "table_id": table_id,
                    "owner_id": owner_id,
                    "fencing_epoch": fencing_epoch,
                    "sequence": 0,
                    "checkpoint": checkpoint,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return commit(transaction)

    def commit_table_action(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
        owner_id: str,
        fencing_epoch: int,
        expected_sequence: int,
        user_id: str,
        action_id: str,
        payload_hash: str,
        checkpoint: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> TableActionCommitResult:
        _validate_table_checkpoint_write(
            scope_type,
            scope_id,
            table_id,
            owner_id,
            fencing_epoch,
            expected_sequence,
            checkpoint,
        )
        try:
            fingerprint_bytes = bytes.fromhex(payload_hash)
        except ValueError as error:
            raise ValueError("Action identity and SHA-256 payload hash are required.") from error
        if not user_id or not action_id or len(fingerprint_bytes) != 32:
            raise ValueError("Action identity and SHA-256 payload hash are required.")

        state_ref = self._table_runtime_state_ref(scope_type, scope_id, table_id)
        lease_ref = self._table_runtime_lease_ref(scope_type, scope_id, table_id)
        action_key = hashlib.sha256(f"{user_id}\0{action_id}".encode("utf-8")).hexdigest()
        action_ref = state_ref.collection("actions").document(action_key)
        transaction = self._client.transaction()
        current_time = now or datetime.now(timezone.utc)

        @firestore.transactional
        def commit(transaction: Any) -> TableActionCommitResult:
            action_snapshot = action_ref.get(transaction=transaction)
            if action_snapshot.exists:
                existing_action = action_snapshot.to_dict() or {}
                status = (
                    TableActionCommitStatus.IDEMPOTENT_REPLAY
                    if existing_action.get("payload_hash") == payload_hash
                    else TableActionCommitStatus.IDEMPOTENCY_CONFLICT
                )
                return TableActionCommitResult(
                    status,
                    int(existing_action.get("sequence", 0)),
                )

            state_snapshot = state_ref.get(transaction=transaction)
            if not state_snapshot.exists:
                return TableActionCommitResult(TableActionCommitStatus.NOT_INITIALIZED, 0)
            current = state_snapshot.to_dict() or {}
            current_sequence = current.get("sequence", 0)
            if current.get("owner_id") != owner_id or current.get("fencing_epoch") != fencing_epoch:
                return TableActionCommitResult(TableActionCommitStatus.FENCED, current_sequence)

            lease_snapshot = lease_ref.get(transaction=transaction)
            lease = lease_snapshot.to_dict() or {} if lease_snapshot.exists else {}
            expires_at = lease.get("lease_expires_at")
            if (
                lease.get("owner_id") != owner_id
                or lease.get("fencing_generation") != fencing_epoch
                or expires_at is None
                or expires_at <= current_time
            ):
                return TableActionCommitResult(TableActionCommitStatus.FENCED, current_sequence)
            if current_sequence != expected_sequence:
                return TableActionCommitResult(
                    TableActionCommitStatus.SEQUENCE_CONFLICT,
                    current_sequence,
                )

            next_sequence = expected_sequence + 1
            transaction.set(
                state_ref,
                {
                    **current,
                    "sequence": next_sequence,
                    "checkpoint": checkpoint,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                action_ref,
                {
                    "user_id": user_id,
                    "action_id": action_id,
                    "payload_hash": payload_hash,
                    "sequence": next_sequence,
                    "fencing_epoch": fencing_epoch,
                    "committed_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return TableActionCommitResult(TableActionCommitStatus.COMMITTED, next_sequence)

        return commit(transaction)

    def _table_runtime_state_ref(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
    ) -> Any:
        if scope_type not in {"club", "union"} or not scope_id or not table_id:
            raise ValueError("Table scope must be club or union and IDs must be non-empty.")
        root = self._client.collection("clubs" if scope_type == "club" else "unions")
        return (
            root.document(scope_id)
            .collection("tables")
            .document(table_id)
            .collection("runtime")
            .document("state")
        )

    def _table_runtime_lease_ref(
        self,
        scope_type: str,
        scope_id: str,
        table_id: str,
    ) -> Any:
        if scope_type not in {"club", "union"} or not scope_id or not table_id:
            raise ValueError("Table scope must be club or union and IDs must be non-empty.")
        root = self._client.collection("clubs" if scope_type == "club" else "unions")
        return (
            root.document(scope_id)
            .collection("tables")
            .document(table_id)
            .collection("runtime")
            .document("lease")
        )

    def ensure_global_wallet(self, user_id: str) -> tuple[dict[str, Any], bool]:
        wallet_ref = self._client.collection("global_wallets").document(user_id)
        grant_ref = wallet_ref.collection("transactions").document("initial-grant")
        reserve_ref = self._client.collection("platform_wallets").document("global-playmoney-reserve")
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> tuple[dict[str, Any], bool]:
            wallet_snapshot = wallet_ref.get(transaction=transaction)
            if wallet_snapshot.exists:
                wallet = wallet_snapshot.to_dict() or {}
                return wallet, False

            reserve_snapshot = reserve_ref.get(transaction=transaction)
            reserve_wallet = reserve_snapshot.to_dict() or {}
            if not reserve_snapshot.exists:
                reserve_wallet = {
                    "wallet_id": "global-playmoney-reserve",
                    "wallet_type": "PLATFORM_RESERVE",
                    "currency_type": "GLOBAL_PLAYMONEY",
                    "chip_balance": 1_000_000_000,
                }
            reserve_balance = reserve_wallet.get("chip_balance", 0)
            if isinstance(reserve_balance, bool) or not isinstance(reserve_balance, int) or reserve_balance < INITIAL_GLOBAL_PLAYMONEY_FUNDS:
                raise ValueError("Platform reserve does not have enough funds for an initial player grant.")

            wallet = {
                "wallet_id": f"global-wallet-{user_id}",
                "wallet_type": "GLOBAL_PLAYMONEY",
                "currency_type": "GLOBAL_PLAYMONEY",
                "owner_user_id": user_id,
                "chip_balance": INITIAL_GLOBAL_PLAYMONEY_FUNDS,
            }
            transaction.set(
                reserve_ref,
                {
                    **reserve_wallet,
                    "wallet_id": "global-playmoney-reserve",
                    "wallet_type": "PLATFORM_RESERVE",
                    "currency_type": "GLOBAL_PLAYMONEY",
                    "chip_balance": reserve_balance - INITIAL_GLOBAL_PLAYMONEY_FUNDS,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            transaction.set(
                wallet_ref,
                {
                    **wallet,
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                grant_ref,
                {
                    "transaction_id": "initial-grant",
                    "wallet_type": "GLOBAL_PLAYMONEY",
                    "currency_type": "GLOBAL_PLAYMONEY",
                    "type": "INITIAL_GRANT",
                    "amount": INITIAL_GLOBAL_PLAYMONEY_FUNDS,
                    "from_wallet_id": "global-playmoney-reserve",
                    "to_wallet_id": f"global-wallet-{user_id}",
                    "from_wallet_type": "PLATFORM_RESERVE",
                    "to_wallet_type": "GLOBAL_PLAYMONEY",
                    "from_user_id": "platform",
                    "to_user_id": user_id,
                    "performed_by": "system",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "balance_after": INITIAL_GLOBAL_PLAYMONEY_FUNDS,
                    "reserve_balance_after": reserve_balance - INITIAL_GLOBAL_PLAYMONEY_FUNDS,
                },
            )
            return wallet, True

        return commit(transaction)

    def get_global_wallet(self, user_id: str) -> dict[str, Any] | None:
        snapshot = self._client.collection("global_wallets").document(user_id).get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict() or {}

    def ensure_user_profile(
        self, user_id: str, username: str
    ) -> tuple[dict[str, Any], bool]:
        profile_ref = self._client.collection("users").document(user_id)
        player_indexes = self._client.collection("player_id_index")

        for offset in range(ID_ALLOCATION_ATTEMPTS):
            candidate_player_id = epoch_8_digit_id(offset)
            index_ref = player_indexes.document(candidate_player_id)
            transaction = self._client.transaction()

            @firestore.transactional
            def commit(transaction: Any) -> tuple[dict[str, Any], bool] | None:
                profile_snapshot = profile_ref.get(transaction=transaction)
                profile = profile_snapshot.to_dict() or {}
                if profile_snapshot.exists and profile.get("player_id"):
                    existing_player_id = str(profile["player_id"])
                    existing_index_ref = player_indexes.document(existing_player_id)
                    existing_index = existing_index_ref.get(transaction=transaction)
                    if existing_index.exists:
                        index_data = existing_index.to_dict() or {}
                        if index_data.get("user_id") != user_id:
                            raise ValueError("Existing player_id is assigned to another user.")
                    else:
                        transaction.set(
                            existing_index_ref,
                            {
                                "player_id": existing_player_id,
                                "user_id": user_id,
                                "created_at": firestore.SERVER_TIMESTAMP,
                            },
                        )
                    return profile, False

                index_snapshot = index_ref.get(transaction=transaction)
                if index_snapshot.exists:
                    index_data = index_snapshot.to_dict() or {}
                    if index_data.get("user_id") == user_id:
                        return profile, False
                    return None

                profile_data = {
                    **profile,
                    "user_id": user_id,
                    "player_id": candidate_player_id,
                    "username": username,
                    "created_at": profile.get("created_at", firestore.SERVER_TIMESTAMP),
                    "updated_at": firestore.SERVER_TIMESTAMP,
                    "created_by": profile.get("created_by", user_id),
                    "updated_by": user_id,
                }
                transaction.set(profile_ref, profile_data, merge=True)
                transaction.set(
                    index_ref,
                    {
                        "player_id": candidate_player_id,
                        "user_id": user_id,
                        "created_at": firestore.SERVER_TIMESTAMP,
                    },
                )
                return profile_data, True

            result = commit(transaction)
            if result is not None:
                return result

        raise RuntimeError("Could not allocate a unique 8-digit player_id.")

    def get_user_profile_by_player_id(self, player_id: str) -> dict[str, Any] | None:
        index_snapshot = (
            self._client.collection("player_id_index").document(player_id).get()
        )
        if not index_snapshot.exists:
            return None
        index_data = index_snapshot.to_dict() or {}
        user_id = index_data.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            return None
        profile_snapshot = self._client.collection("users").document(user_id).get()
        if not profile_snapshot.exists:
            return None
        profile = profile_snapshot.to_dict() or {}
        if profile.get("player_id") != player_id:
            return None
        return profile

    def create_club(
        self,
        club_uuid: str,
        club_id: str,
        name: str,
        owner_user_id: str,
        created_by: str,
    ) -> tuple[bool, str]:
        owner_profile, _ = self.ensure_user_profile(owner_user_id, owner_user_id)
        owner_player_id = str(owner_profile["player_id"])
        club_ref = self._client.collection("clubs").document(club_uuid)
        club_index_ref = self._client.collection("club_id_index").document(club_id)
        owner_ref = club_ref.collection("members").document(owner_user_id)
        wallet_ref = club_ref.collection("treasury").document("wallet")
        initial_funding_ref = club_ref.collection("transactions").document("initial-funding")
        reserve_ref = self._client.collection("platform_wallets").document("global-playmoney-reserve")
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            club_snapshot = club_ref.get(transaction=transaction)
            club_index_snapshot = club_index_ref.get(transaction=transaction)
            if club_snapshot.exists or club_index_snapshot.exists:
                return False, owner_player_id

            reserve_snapshot = reserve_ref.get(transaction=transaction)
            reserve_wallet = reserve_snapshot.to_dict() or {}
            if not reserve_snapshot.exists:
                reserve_wallet = {
                    "wallet_id": "global-playmoney-reserve",
                    "wallet_type": "PLATFORM_RESERVE",
                    "currency_type": "GLOBAL_PLAYMONEY",
                    "chip_balance": 1_000_000_000,
                }
            reserve_balance = reserve_wallet.get("chip_balance", 0)
            if isinstance(reserve_balance, bool) or not isinstance(reserve_balance, int) or reserve_balance < INITIAL_CLUB_TREASURY_FUNDS:
                raise ValueError("Platform reserve does not have enough funds for an initial club grant.")

            transaction.set(
                club_ref,
                {
                    "club_uuid": club_uuid,
                    "club_id": club_id,
                    "name": name,
                    "status": "active",
                    "owner_user_id": owner_user_id,
                    "owner_player_id": owner_player_id,
                    "created_by": created_by,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                club_index_ref,
                {
                    "club_id": club_id,
                    "club_uuid": club_uuid,
                    "name": name,
                    "status": "active",
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "created_by": created_by,
                },
            )
            transaction.set(
                owner_ref,
                {
                    "user_id": owner_user_id,
                    "player_id": owner_player_id,
                    "club_uuid": club_uuid,
                    "club_id": club_id,
                    "role": "OWNER",
                    "status": "active",
                    "chip_balance": 0,
                    "in_play_balance": 0,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                reserve_ref,
                {
                    **reserve_wallet,
                    "wallet_id": "global-playmoney-reserve",
                    "wallet_type": "PLATFORM_RESERVE",
                    "currency_type": "GLOBAL_PLAYMONEY",
                    "chip_balance": reserve_balance - INITIAL_CLUB_TREASURY_FUNDS,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            transaction.set(
                wallet_ref,
                {"chip_balance": INITIAL_CLUB_TREASURY_FUNDS},
            )
            transaction.set(
                initial_funding_ref,
                {
                    "transaction_id": "initial-funding",
                    "currency_type": "CLUB_CHIPS",
                    "type": "INITIAL_GRANT",
                    "from_wallet_id": "global-playmoney-reserve",
                    "to_wallet_id": f"club-treasury-{club_id}",
                    "from_wallet_type": "PLATFORM_RESERVE",
                    "to_wallet_type": "CLUB_TREASURY",
                    "to_club_id": club_id,
                    "to_club_uuid": club_uuid,
                    "amount": INITIAL_CLUB_TREASURY_FUNDS,
                    "performed_by": created_by,
                    "note": "Initial club treasury funding",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                    "reserve_balance_after": reserve_balance - INITIAL_CLUB_TREASURY_FUNDS,
                },
            )
            return True, owner_player_id

        return commit(transaction)

    def create_agent_group(
        self,
        club_uuid: str,
        club_id: str,
        agent_group_id: str,
        agent_player_id: str,
        created_by: str,
    ) -> dict[str, Any] | None:
        agent_profile = self.get_user_profile_by_player_id(agent_player_id)
        if agent_profile is None:
            return None
        agent_user_id = str(agent_profile["user_id"])
        club_ref = self._client.collection("clubs").document(club_uuid)
        agent_member_ref = club_ref.collection("members").document(agent_user_id)
        group_ref = self._client.collection("agent_groups").document(agent_group_id)
        group_index_id = f"{club_uuid}_{agent_player_id}"
        group_index_ref = self._client.collection("agent_group_index").document(group_index_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> dict[str, Any] | None:
            club_snapshot = club_ref.get(transaction=transaction)
            member_snapshot = agent_member_ref.get(transaction=transaction)
            group_index_snapshot = group_index_ref.get(transaction=transaction)
            if not club_snapshot.exists or not member_snapshot.exists:
                return None
            club = club_snapshot.to_dict() or {}
            member = member_snapshot.to_dict() or {}
            if club.get("club_id") != club_id:
                return None
            if str(member.get("role", "")).upper() != "AGENT" or member.get("status") != "active":
                return None

            if group_index_snapshot.exists:
                index_data = group_index_snapshot.to_dict() or {}
                existing_group_id = index_data.get("agent_group_id")
                if not isinstance(existing_group_id, str) or not existing_group_id:
                    return None
                existing_group_ref = self._client.collection("agent_groups").document(
                    existing_group_id
                )
                existing_group_snapshot = existing_group_ref.get(transaction=transaction)
                existing_group = existing_group_snapshot.to_dict() or {}
                if (
                    existing_group_snapshot.exists
                    and existing_group.get("club_uuid") == club_uuid
                    and existing_group.get("agent_player_id") == agent_player_id
                ):
                    return existing_group
                return None

            group = {
                "agent_group_id": agent_group_id,
                "club_uuid": club_uuid,
                "club_id": club_id,
                "agent_player_id": agent_player_id,
                "agent_user_id": agent_user_id,
                "acting_agent_player_id": agent_player_id,
                "acting_agent_user_id": agent_user_id,
                "agent_balance": 0,
                "rakeback_balance": 0,
                "status": "active",
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "created_by": created_by,
                "updated_by": created_by,
            }
            transaction.set(group_ref, group)
            transaction.set(
                group_index_ref,
                {
                    "agent_group_id": agent_group_id,
                    "club_uuid": club_uuid,
                    "club_id": club_id,
                    "agent_player_id": agent_player_id,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.update(
                agent_member_ref,
                {
                    "agent_group_id": agent_group_id,
                    "agent_player_id": agent_player_id,
                    "agent_id": agent_user_id,
                },
            )
            return group

        return commit(transaction)

    def get_club_profile(self, club_uuid: str) -> dict[str, Any] | None:
        snapshot = self._client.collection("clubs").document(club_uuid).get()
        if not snapshot.exists:
            return None
        club = snapshot.to_dict() or {}
        club["club_uuid"] = club_uuid
        return club

    def get_agent_group(self, agent_group_id: str) -> dict[str, Any] | None:
        snapshot = self._client.collection("agent_groups").document(agent_group_id).get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict() or {}

    def add_agent_group_member(
        self,
        club_uuid: str,
        club_id: str,
        agent_group_id: str,
        member_player_id: str,
        assigned_by: str,
    ) -> dict[str, Any] | None:
        member_profile = self.get_user_profile_by_player_id(member_player_id)
        if member_profile is None:
            return None
        member_user_id = str(member_profile["user_id"])
        club_ref = self._client.collection("clubs").document(club_uuid)
        group_ref = self._client.collection("agent_groups").document(agent_group_id)
        member_ref = club_ref.collection("members").document(member_user_id)
        membership_id = hashlib.sha256(
            f"{club_uuid}:{member_player_id}".encode("utf-8")
        ).hexdigest()
        assignment_ref = self._client.collection("agent_group_memberships").document(
            membership_id
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> dict[str, Any] | None:
            group_snapshot = group_ref.get(transaction=transaction)
            member_snapshot = member_ref.get(transaction=transaction)
            assignment_snapshot = assignment_ref.get(transaction=transaction)
            if not group_snapshot.exists or not member_snapshot.exists:
                return None
            group = group_snapshot.to_dict() or {}
            member = member_snapshot.to_dict() or {}
            if (
                group.get("club_uuid") != club_uuid
                or group.get("club_id") != club_id
                or group.get("status") != "active"
                or member.get("status") != "active"
                or str(member.get("role", "")).upper() != "PLAYER"
            ):
                return None
            if assignment_snapshot.exists:
                existing = assignment_snapshot.to_dict() or {}
                return existing if existing.get("agent_group_id") == agent_group_id else None

            assignment = {
                "membership_id": membership_id,
                "agent_group_id": agent_group_id,
                "club_uuid": club_uuid,
                "club_id": club_id,
                "member_player_id": member_player_id,
                "member_user_id": member_user_id,
                "status": "active",
                "created_at": firestore.SERVER_TIMESTAMP,
                "created_by": assigned_by,
            }
            transaction.set(assignment_ref, assignment)
            transaction.update(
                member_ref,
                {
                    "agent_id": group["acting_agent_user_id"],
                    "agent_group_id": agent_group_id,
                    "agent_player_id": group["agent_player_id"],
                },
            )
            return assignment

        return commit(transaction)

    def list_agent_group_members(self, agent_group_id: str) -> list[dict[str, Any]]:
        snapshots = self._client.collection("agent_group_memberships").stream()
        return sorted(
            [
                snapshot.to_dict() or {}
                for snapshot in snapshots
                if (snapshot.to_dict() or {}).get("agent_group_id") == agent_group_id
                and (snapshot.to_dict() or {}).get("status") == "active"
            ],
            key=lambda assignment: str(assignment.get("member_player_id", "")),
        )

    def get_club_wallet_balance(self, club_id: str) -> int:
        wallet_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("treasury")
            .document("wallet")
        )
        snapshot = wallet_ref.get()
        wallet = snapshot.to_dict() or {}
        balance = wallet.get("chip_balance", 0)
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
            raise ValueError("Club treasury wallet balance is invalid.")
        return balance

    def transfer_between_club_wallets(
        self,
        source_club_id: str,
        destination_club_id: str,
        operator_user_id: str,
        amount: int,
        transaction_id: str,
        performed_by: str,
        note: str = "",
    ) -> WalletTransactionStatus:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            return WalletTransactionStatus.INVALID_AMOUNT
        if source_club_id == destination_club_id:
            return WalletTransactionStatus.SAME_CLUB

        clubs_ref = self._client.collection("clubs")
        source_club_ref = clubs_ref.document(source_club_id)
        destination_club_ref = clubs_ref.document(destination_club_id)
        operator_ref = source_club_ref.collection("members").document(operator_user_id)
        source_wallet_ref = source_club_ref.collection("treasury").document("wallet")
        destination_wallet_ref = destination_club_ref.collection("treasury").document("wallet")
        source_ledger_ref = source_club_ref.collection("transactions").document(transaction_id)
        destination_ledger_ref = destination_club_ref.collection("transactions").document(transaction_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> WalletTransactionStatus:
            source_club_snapshot = source_club_ref.get(transaction=transaction)
            destination_club_snapshot = destination_club_ref.get(transaction=transaction)
            operator_snapshot = operator_ref.get(transaction=transaction)
            source_wallet_snapshot = source_wallet_ref.get(transaction=transaction)
            destination_wallet_snapshot = destination_wallet_ref.get(transaction=transaction)
            source_ledger_snapshot = source_ledger_ref.get(transaction=transaction)
            destination_ledger_snapshot = destination_ledger_ref.get(transaction=transaction)

            if source_ledger_snapshot.exists or destination_ledger_snapshot.exists:
                source_entry = source_ledger_snapshot.to_dict() or {}
                destination_entry = destination_ledger_snapshot.to_dict() or {}
                same_request = (
                    source_entry.get("type") == "INTER_CLUB_SEND_OUT"
                    and destination_entry.get("type") == "INTER_CLUB_RECEIVE"
                    and source_entry.get("from_club_id") == source_club_id
                    and source_entry.get("to_club_id") == destination_club_id
                    and destination_entry.get("from_club_id") == source_club_id
                    and destination_entry.get("to_club_id") == destination_club_id
                    and source_entry.get("amount") == amount
                    and destination_entry.get("amount") == amount
                )
                return WalletTransactionStatus.IDEMPOTENT_REPLAY if same_request else WalletTransactionStatus.IDEMPOTENCY_CONFLICT

            if not source_club_snapshot.exists or not destination_club_snapshot.exists:
                return WalletTransactionStatus.CLUB_NOT_FOUND
            if not operator_snapshot.exists:
                return WalletTransactionStatus.MEMBER_NOT_FOUND

            operator = operator_snapshot.to_dict() or {}
            if operator.get("status") != "active" or str(operator.get("role", "")).upper() not in {"OWNER", "MANAGER"}:
                return WalletTransactionStatus.UNAUTHORIZED_OPERATOR

            source_wallet = source_wallet_snapshot.to_dict() or {}
            destination_wallet = destination_wallet_snapshot.to_dict() or {}
            source_balance = source_wallet.get("chip_balance", 0)
            destination_balance = destination_wallet.get("chip_balance", 0)
            if any(
                isinstance(balance, bool) or not isinstance(balance, int) or balance < 0
                for balance in (source_balance, destination_balance)
            ):
                return WalletTransactionStatus.INVALID_BALANCE
            if source_balance < amount:
                return WalletTransactionStatus.INSUFFICIENT_BALANCE

            transaction.update(
                source_wallet_ref,
                {"chip_balance": firestore.Increment(-amount)},
            )
            transaction.set(
                destination_wallet_ref,
                {"chip_balance": firestore.Increment(amount)},
                merge=True,
            )
            transfer_record = {
                "transaction_id": transaction_id,
                "currency_type": "CLUB_CHIPS",
                "from_club_id": source_club_id,
                "to_club_id": destination_club_id,
                "amount": amount,
                "performed_by": performed_by,
                "note": note,
                "timestamp": firestore.SERVER_TIMESTAMP,
            }
            transaction.set(
                source_ledger_ref,
                {**transfer_record, "type": "INTER_CLUB_SEND_OUT"},
            )
            transaction.set(
                destination_ledger_ref,
                {**transfer_record, "type": "INTER_CLUB_RECEIVE"},
            )
            return WalletTransactionStatus.COMPLETED

        return commit(transaction)

    def get_club_member(self, club_id: str, user_id: str) -> dict[str, Any] | None:
        member_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("members")
            .document(user_id)
        )
        snapshot = member_ref.get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict()

    def list_club_player_rake_stats(
        self, club_id: str, agent_user_id: str | None = None
    ) -> list[dict[str, Any]]:
        stats = [
            snapshot.to_dict() or {}
            for snapshot in self._client.collection("clubs")
            .document(club_id)
            .collection("player_rake_stats")
            .stream()
        ]
        if agent_user_id is not None:
            agent_group_ids = {
                str(group.get("agent_group_id"))
                for snapshot in self._client.collection("agent_groups").stream()
                if (group := snapshot.to_dict() or {}).get("club_uuid") == club_id
                and group.get("acting_agent_user_id") == agent_user_id
                and group.get("status") == "active"
            }
            assigned_user_ids = {
                str(assignment["member_user_id"])
                for snapshot in self._client.collection("agent_group_memberships").stream()
                if (assignment := snapshot.to_dict() or {}).get("agent_group_id")
                in agent_group_ids
                and assignment.get("status") == "active"
                and assignment.get("member_user_id")
            }
            stats = [
                player_stats
                for player_stats in stats
                if player_stats.get("user_id") in assigned_user_ids
            ]
        return sorted(stats, key=lambda player_stats: str(player_stats.get("user_id", "")))

    def enqueue_pending_hand(
        self,
        hand_ref: Any,
        hand_summary: dict[str, Any],
    ) -> bool:
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            snapshot = hand_ref.get(transaction=transaction)
            if snapshot.exists:
                existing = snapshot.to_dict() or {}
                return existing.get("settlement_status") == "pending"
            transaction.set(
                hand_ref,
                {
                    **hand_summary,
                    "settlement_status": "pending",
                    "settlement_enqueued_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return commit(transaction)

    def recover_pending_settlements(self) -> int:
        pending_hands = (
            self._client.collection_group("hands")
            .where("settlement_status", "==", "pending")
            .stream()
        )
        recovered = 0
        for snapshot in pending_hands:
            hand = snapshot.to_dict() or {}
            players = hand.get("players", [])
            chip_deltas = {
                str(player["user_id"]): int(player["net_delta"])
                for player in players
                if player.get("user_id") is not None and player.get("net_delta") is not None
            }
            union_id = hand.get("union_id")
            if union_id:
                player_clubs = {
                    str(player["user_id"]): str(player["club_id"])
                    for player in players
                    if player.get("user_id") is not None and player.get("club_id") is not None
                }
                completed = self.persist_union_hand_history_and_ledgers(
                    str(union_id),
                    str(hand["table_id"]),
                    hand,
                    chip_deltas,
                    player_clubs,
                )
            else:
                completed = self.persist_hand_history_and_ledgers(
                    str(hand["club_id"]),
                    str(hand["table_id"]),
                    hand,
                    chip_deltas,
                )
            if completed:
                recovered += 1
        return recovered

    def send_out_club_chips(
        self,
        club_id: str,
        sender_user_id: str,
        recipient_user_id: str,
        amount: int,
        transaction_id: str,
        performed_by: str,
        note: str = "",
    ) -> WalletTransactionStatus:
        club_ref = self._client.collection("clubs").document(club_id)
        sender_ref = club_ref.collection("members").document(sender_user_id)
        recipient_ref = club_ref.collection("members").document(recipient_user_id)
        ledger_ref = club_ref.collection("transactions").document(transaction_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> WalletTransactionStatus:
            prior_snapshot = ledger_ref.get(transaction=transaction)
            if prior_snapshot.exists:
                prior = prior_snapshot.to_dict() or {}
                same_request = (
                    prior.get("type") == "SEND_OUT"
                    and prior.get("from_user_id") == sender_user_id
                    and prior.get("to_user_id") == recipient_user_id
                    and prior.get("amount") == amount
                )
                return WalletTransactionStatus.IDEMPOTENT_REPLAY if same_request else WalletTransactionStatus.IDEMPOTENCY_CONFLICT

            sender_snapshot = sender_ref.get(transaction=transaction)
            recipient_snapshot = recipient_ref.get(transaction=transaction)
            if not sender_snapshot.exists or not recipient_snapshot.exists:
                return WalletTransactionStatus.MEMBER_NOT_FOUND

            sender = sender_snapshot.to_dict() or {}
            recipient = recipient_snapshot.to_dict() or {}
            role = str(sender.get("role", "")).upper()
            recipient_role = str(recipient.get("role", "")).upper()
            if role not in {"OWNER", "MANAGER", "AGENT"}:
                return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
            if role == "AGENT" and recipient.get("agent_id") != sender_user_id:
                return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE

            sender_group_ref = None
            sender_group = None
            if role == "AGENT":
                agent_group_id = sender.get("agent_group_id")
                if not isinstance(agent_group_id, str) or not agent_group_id:
                    return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
                sender_group_ref = self._client.collection("agent_groups").document(agent_group_id)
                sender_group_snapshot = sender_group_ref.get(transaction=transaction)
                sender_group = sender_group_snapshot.to_dict() or {}
                if (
                    not sender_group_snapshot.exists
                    or sender_group.get("status") != "active"
                    or sender_group.get("acting_agent_user_id") != sender_user_id
                    or sender_group.get("club_uuid") != club_id
                ):
                    return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE

            recipient_group_ref = None
            recipient_group = None
            if recipient_role == "AGENT":
                recipient_group_id = recipient.get("agent_group_id")
                if not isinstance(recipient_group_id, str) or not recipient_group_id:
                    return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
                recipient_group_ref = self._client.collection("agent_groups").document(recipient_group_id)
                recipient_group_snapshot = recipient_group_ref.get(transaction=transaction)
                recipient_group = recipient_group_snapshot.to_dict() or {}
                if (
                    not recipient_group_snapshot.exists
                    or recipient_group.get("status") != "active"
                    or recipient_group.get("club_uuid") != club_id
                ):
                    return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE

            balance = (
                sender_group.get("agent_balance", 0)
                if sender_group is not None
                else sender.get("chip_balance", 0)
            )
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
                return WalletTransactionStatus.INVALID_BALANCE
            if balance < amount:
                return WalletTransactionStatus.INSUFFICIENT_BALANCE

            if sender_group_ref is not None:
                transaction.update(
                    sender_group_ref,
                    {"agent_balance": firestore.Increment(-amount), "updated_at": firestore.SERVER_TIMESTAMP},
                )
            else:
                transaction.update(sender_ref, {"chip_balance": firestore.Increment(-amount)})
            if recipient_group_ref is not None:
                transaction.update(
                    recipient_group_ref,
                    {"agent_balance": firestore.Increment(amount), "updated_at": firestore.SERVER_TIMESTAMP},
                )
            else:
                transaction.update(recipient_ref, {"chip_balance": firestore.Increment(amount)})
            transaction.set(
                ledger_ref,
                {
                    "transaction_id": transaction_id,
                    "currency_type": "CLUB_CHIPS",
                    "type": "SEND_OUT",
                    "amount": amount,
                    "from_user_id": sender_user_id,
                    "to_user_id": recipient_user_id,
                    "performed_by": performed_by,
                    "from_agent_group_id": sender_group.get("agent_group_id") if sender_group else None,
                    "to_agent_group_id": recipient_group.get("agent_group_id") if recipient_group else None,
                    "note": note,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                },
            )
            return WalletTransactionStatus.COMPLETED

        return commit(transaction)

    def claim_back_club_chips(
        self,
        club_id: str,
        player_user_id: str,
        recipient_user_id: str,
        amount: int,
        transaction_id: str,
        performed_by: str,
        note: str = "",
    ) -> WalletTransactionStatus:
        club_ref = self._client.collection("clubs").document(club_id)
        player_ref = club_ref.collection("members").document(player_user_id)
        recipient_ref = club_ref.collection("members").document(recipient_user_id)
        ledger_ref = club_ref.collection("transactions").document(transaction_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> WalletTransactionStatus:
            prior_snapshot = ledger_ref.get(transaction=transaction)
            if prior_snapshot.exists:
                prior = prior_snapshot.to_dict() or {}
                same_request = (
                    prior.get("type") == "CLAIM_BACK"
                    and prior.get("from_user_id") == player_user_id
                    and prior.get("to_user_id") == recipient_user_id
                    and prior.get("amount") == amount
                )
                return WalletTransactionStatus.IDEMPOTENT_REPLAY if same_request else WalletTransactionStatus.IDEMPOTENCY_CONFLICT

            player_snapshot = player_ref.get(transaction=transaction)
            recipient_snapshot = recipient_ref.get(transaction=transaction)
            if not player_snapshot.exists or not recipient_snapshot.exists:
                return WalletTransactionStatus.MEMBER_NOT_FOUND

            player = player_snapshot.to_dict() or {}
            recipient = recipient_snapshot.to_dict() or {}
            role = str(recipient.get("role", "")).upper()
            if role not in {"OWNER", "MANAGER", "AGENT"}:
                return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
            if role == "AGENT" and player.get("agent_id") != recipient_user_id:
                return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE

            recipient_group_ref = None
            recipient_group = None
            if role == "AGENT":
                agent_group_id = recipient.get("agent_group_id")
                if not isinstance(agent_group_id, str) or not agent_group_id:
                    return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
                recipient_group_ref = self._client.collection("agent_groups").document(agent_group_id)
                recipient_group_snapshot = recipient_group_ref.get(transaction=transaction)
                recipient_group = recipient_group_snapshot.to_dict() or {}
                if (
                    not recipient_group_snapshot.exists
                    or recipient_group.get("status") != "active"
                    or recipient_group.get("acting_agent_user_id") != recipient_user_id
                    or recipient_group.get("club_uuid") != club_id
                ):
                    return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE

            balance = player.get("chip_balance", 0)
            in_play = player.get("in_play_balance", 0)
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (balance, in_play)):
                return WalletTransactionStatus.INVALID_BALANCE
            if in_play > 0:
                return WalletTransactionStatus.PLAYER_HAS_CHIPS_IN_PLAY
            if balance < amount:
                return WalletTransactionStatus.INSUFFICIENT_BALANCE

            transaction.update(player_ref, {"chip_balance": firestore.Increment(-amount)})
            if recipient_group_ref is not None:
                transaction.update(
                    recipient_group_ref,
                    {"agent_balance": firestore.Increment(amount), "updated_at": firestore.SERVER_TIMESTAMP},
                )
            else:
                transaction.update(recipient_ref, {"chip_balance": firestore.Increment(amount)})
            transaction.set(
                ledger_ref,
                {
                    "transaction_id": transaction_id,
                    "currency_type": "CLUB_CHIPS",
                    "type": "CLAIM_BACK",
                    "amount": amount,
                    "from_user_id": player_user_id,
                    "to_user_id": recipient_user_id,
                    "performed_by": performed_by,
                    "to_agent_group_id": recipient_group.get("agent_group_id") if recipient_group else None,
                    "note": note,
                    "timestamp": firestore.SERVER_TIMESTAMP,
                },
            )
            return WalletTransactionStatus.COMPLETED

        return commit(transaction)

    def get_hand_history(self, club_id: str, hand_id: str) -> dict[str, Any] | None:
        matches = (
            self._client.collection_group("hands")
            .where("hand_id", "==", hand_id)
            .stream()
        )
        for snapshot in matches:
            hand = snapshot.to_dict() or {}
            if hand.get("club_id") == club_id:
                return hand
        return None

    def get_union_hand_history(self, union_id: str, hand_id: str) -> dict[str, Any] | None:
        matches = (
            self._client.collection_group("hands")
            .where("hand_id", "==", hand_id)
            .stream()
        )
        for snapshot in matches:
            hand = snapshot.to_dict() or {}
            if hand.get("union_id") == union_id:
                return hand
        return None

    def get_union_admin(self, union_id: str, user_id: str) -> dict[str, Any] | None:
        snapshot = (
            self._client.collection("unions")
            .document(union_id)
            .collection("admins")
            .document(user_id)
            .get()
        )
        if not snapshot.exists:
            return None
        return snapshot.to_dict()

    def get_union_profile(self, union_id: str) -> dict[str, Any] | None:
        snapshot = self._client.collection("unions").document(union_id).get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict()

    def get_union_by_code(self, union_code: str) -> dict[str, Any] | None:
        snapshot = self._client.collection("union_codes").document(union_code).get()
        if not snapshot.exists:
            return None
        code_record = snapshot.to_dict() or {}
        union_id = code_record.get("union_id")
        if not isinstance(union_id, str) or not union_id:
            return None
        union = self.get_union_profile(union_id)
        if union is None:
            return None
        return {**union, "union_code": union_code}

    def create_union(
        self,
        union_id: str,
        union_data: dict[str, Any],
        union_code: str,
        creator_user_id: str,
        founding_club_id: str,
    ) -> bool:
        union_ref = self._client.collection("unions").document(union_id)
        code_ref = self._client.collection("union_codes").document(union_code)
        admin_ref = union_ref.collection("admins").document(creator_user_id)
        representative_ref = union_ref.collection("representatives").document(creator_user_id)
        club_ref = union_ref.collection("clubs").document(founding_club_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            union_snapshot = union_ref.get(transaction=transaction)
            code_snapshot = code_ref.get(transaction=transaction)
            if union_snapshot.exists or code_snapshot.exists:
                return False
            transaction.set(
                union_ref,
                {**union_data, "created_at": firestore.SERVER_TIMESTAMP},
            )
            transaction.set(
                code_ref,
                {"union_id": union_id, "union_code": union_code},
            )
            transaction.set(
                admin_ref,
                {"user_id": creator_user_id, "role": "UNION_ADMIN", "status": "active"},
            )
            transaction.set(
                representative_ref,
                {
                    "user_id": creator_user_id,
                    "club_id": founding_club_id,
                    "status": "active",
                    "created_by": creator_user_id,
                },
            )
            transaction.set(
                club_ref,
                {
                    "union_id": union_id,
                    "club_id": founding_club_id,
                    "union_name": union_data["name"],
                    "union_code": union_code,
                    "theme_id": union_data["theme_id"],
                    "status": "active",
                    "created_by": creator_user_id,
                },
            )
            return True

        return commit(transaction)

    def get_union_representative(
        self, union_id: str, user_id: str
    ) -> dict[str, Any] | None:
        snapshot = (
            self._client.collection("unions")
            .document(union_id)
            .collection("representatives")
            .document(user_id)
            .get()
        )
        return snapshot.to_dict() if snapshot.exists else None

    def request_union_representative(
        self, union_id: str, user_id: str, club_id: str, club_role: str
    ) -> UnionRequestStatus:
        union_ref = self._client.collection("unions").document(union_id)
        representative_ref = union_ref.collection("representatives").document(user_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> UnionRequestStatus:
            union_snapshot = union_ref.get(transaction=transaction)
            if not union_snapshot.exists or (union_snapshot.to_dict() or {}).get("status") != "active":
                return UnionRequestStatus.NOT_FOUND
            representative_snapshot = representative_ref.get(transaction=transaction)
            representative = representative_snapshot.to_dict() or {} if representative_snapshot.exists else {}
            if representative.get("status") == "active":
                return UnionRequestStatus.ACTIVE
            transaction.set(
                representative_ref,
                {
                    "user_id": user_id,
                    "club_id": club_id,
                    "club_role": club_role,
                    "status": "pending",
                    "requested_at": firestore.SERVER_TIMESTAMP,
                },
            )
            return UnionRequestStatus.PENDING

        return commit(transaction)

    def list_union_representative_requests(self, union_id: str) -> list[dict[str, Any]]:
        snapshots = (
            self._client.collection("unions")
            .document(union_id)
            .collection("representatives")
            .stream()
        )
        return sorted(
            [data for snapshot in snapshots if (data := snapshot.to_dict() or {}).get("status") == "pending"],
            key=lambda request: str(request.get("user_id", "")),
        )

    def approve_union_representative(
        self, union_id: str, user_id: str, approved_by: str
    ) -> UnionRequestStatus:
        union_ref = self._client.collection("unions").document(union_id)
        representative_ref = union_ref.collection("representatives").document(user_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> UnionRequestStatus:
            snapshot = representative_ref.get(transaction=transaction)
            if not snapshot.exists:
                return UnionRequestStatus.NOT_FOUND
            representative = snapshot.to_dict() or {}
            if representative.get("status") == "active":
                return UnionRequestStatus.ACTIVE
            if representative.get("status") != "pending":
                return UnionRequestStatus.NOT_FOUND
            club_id = representative.get("club_id")
            if not isinstance(club_id, str) or not club_id:
                return UnionRequestStatus.NOT_AUTHORIZED
            member_ref = (
                self._client.collection("clubs")
                .document(club_id)
                .collection("members")
                .document(user_id)
            )
            member_snapshot = member_ref.get(transaction=transaction)
            member = member_snapshot.to_dict() or {} if member_snapshot.exists else {}
            if member.get("status") != "active" or member.get("role") not in {"OWNER", "MANAGER"}:
                return UnionRequestStatus.NOT_AUTHORIZED
            transaction.set(
                representative_ref,
                {"status": "active", "approved_by": approved_by, "approved_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            return UnionRequestStatus.ACTIVE

        return commit(transaction)

    def request_union_club(
        self,
        union_id: str,
        club_id: str,
        union_name: str,
        union_code: str,
        requested_by: str,
    ) -> UnionRequestStatus:
        union_ref = self._client.collection("unions").document(union_id)
        representative_ref = union_ref.collection("representatives").document(requested_by)
        club_ref = union_ref.collection("clubs").document(club_id)
        member_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("members")
            .document(requested_by)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> UnionRequestStatus:
            union_snapshot = union_ref.get(transaction=transaction)
            representative_snapshot = representative_ref.get(transaction=transaction)
            club_snapshot = club_ref.get(transaction=transaction)
            member_snapshot = member_ref.get(transaction=transaction)
            if not union_snapshot.exists or (union_snapshot.to_dict() or {}).get("status") != "active":
                return UnionRequestStatus.NOT_FOUND
            if (
                not representative_snapshot.exists
                or (representative_snapshot.to_dict() or {}).get("status") != "active"
                or (representative_snapshot.to_dict() or {}).get("club_id") != club_id
            ):
                return UnionRequestStatus.NOT_AUTHORIZED
            member = member_snapshot.to_dict() or {} if member_snapshot.exists else {}
            if member.get("status") != "active" or member.get("role") not in {"OWNER", "MANAGER"}:
                return UnionRequestStatus.NOT_AUTHORIZED
            if club_snapshot.exists:
                status = (club_snapshot.to_dict() or {}).get("status")
                if status == "active":
                    return UnionRequestStatus.ACTIVE
                if status == "pending":
                    return UnionRequestStatus.PENDING
            transaction.set(
                club_ref,
                {
                    "union_id": union_id,
                    "club_id": club_id,
                    "union_name": union_name,
                    "union_code": union_code,
                    "status": "pending",
                    "requested_by": requested_by,
                    "requested_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return UnionRequestStatus.PENDING

        return commit(transaction)

    def list_union_club_requests(self, union_id: str) -> list[dict[str, Any]]:
        snapshots = (
            self._client.collection("unions")
            .document(union_id)
            .collection("clubs")
            .stream()
        )
        return sorted(
            [data for snapshot in snapshots if (data := snapshot.to_dict() or {}).get("status") == "pending"],
            key=lambda request: str(request.get("club_id", "")),
        )

    def approve_union_club(
        self, union_id: str, club_id: str, approved_by: str
    ) -> UnionRequestStatus:
        club_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("clubs")
            .document(club_id)
        )
        union_ref = self._client.collection("unions").document(union_id)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> UnionRequestStatus:
            snapshot = club_ref.get(transaction=transaction)
            if not snapshot.exists:
                return UnionRequestStatus.NOT_FOUND
            club = snapshot.to_dict() or {}
            if club.get("status") == "active":
                return UnionRequestStatus.ACTIVE
            if club.get("status") != "pending":
                return UnionRequestStatus.NOT_FOUND
            requested_by = club.get("requested_by")
            if not isinstance(requested_by, str) or not requested_by:
                return UnionRequestStatus.NOT_AUTHORIZED
            representative_ref = union_ref.collection("representatives").document(requested_by)
            member_ref = (
                self._client.collection("clubs")
                .document(club_id)
                .collection("members")
                .document(requested_by)
            )
            representative_snapshot = representative_ref.get(transaction=transaction)
            member_snapshot = member_ref.get(transaction=transaction)
            representative = representative_snapshot.to_dict() or {} if representative_snapshot.exists else {}
            member = member_snapshot.to_dict() or {} if member_snapshot.exists else {}
            if (
                representative.get("status") != "active"
                or representative.get("club_id") != club_id
                or member.get("status") != "active"
                or member.get("role") not in {"OWNER", "MANAGER"}
            ):
                return UnionRequestStatus.NOT_AUTHORIZED
            transaction.set(
                club_ref,
                {"status": "active", "approved_by": approved_by, "approved_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            return UnionRequestStatus.ACTIVE

        return commit(transaction)

    def is_club_in_union(self, union_id: str, club_id: str) -> bool:
        snapshot = (
            self._client.collection("unions")
            .document(union_id)
            .collection("clubs")
            .document(club_id)
            .get()
        )
        if not snapshot.exists:
            return False
        return (snapshot.to_dict() or {}).get("status") == "active"

    def get_unions_for_club(self, club_id: str) -> list[dict[str, str]]:
        snapshots = (
            self._client.collection_group("clubs")
            .where("club_id", "==", club_id)
            .stream()
        )
        unions = [
            {
                "union_id": str(data["union_id"]),
                "name": str(data.get("union_name") or data["union_id"]),
                "union_code": str(data.get("union_code") or ""),
                "theme_id": str(data.get("theme_id") or "standard"),
            }
            for snapshot in snapshots
            if (data := snapshot.to_dict() or {}).get("status") == "active"
            and data.get("union_id")
        ]
        return sorted(unions, key=lambda union: union["union_id"])

    def set_union_admin(
        self,
        union_id: str,
        user_id: str,
        role: str,
        status: str,
    ) -> bool:
        admin_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("admins")
            .document(user_id)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            transaction.set(
                admin_ref,
                {
                    "role": role,
                    "status": status,
                },
                merge=True,
            )
            return True

        return commit(transaction)

    def add_union_club(
        self,
        union_id: str,
        club_id: str,
        club_data: dict[str, Any],
    ) -> bool:
        club_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("clubs")
            .document(club_id)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            transaction.set(club_ref, club_data, merge=True)
            return True

        return commit(transaction)

    def create_union_table_record(
        self,
        union_id: str,
        table_id: str,
        table_data: dict[str, Any],
        *,
        runtime_checkpoint: dict[str, Any] | None = None,
        required_active_club_id: str | None = None,
    ) -> bool:
        table_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("tables")
            .document(table_id)
        )
        state_ref = self._table_runtime_state_ref("union", union_id, table_id)
        club_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("clubs")
            .document(required_active_club_id)
            if required_active_club_id is not None
            else None
        )
        union_ref = (
            self._client.collection("unions").document(union_id)
            if required_active_club_id is not None
            else None
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            table_snapshot = table_ref.get(transaction=transaction)
            state_snapshot = state_ref.get(transaction=transaction)
            club_snapshot = (
                club_ref.get(transaction=transaction)
                if club_ref is not None
                else None
            )
            union_snapshot = (
                union_ref.get(transaction=transaction)
                if union_ref is not None
                else None
            )
            if table_snapshot.exists or (runtime_checkpoint is not None and state_snapshot.exists):
                return False
            if club_snapshot is not None and (
                not club_snapshot.exists
                or (club_snapshot.to_dict() or {}).get("status") != "active"
            ):
                return False
            if union_snapshot is not None and (
                not union_snapshot.exists
                or (union_snapshot.to_dict() or {}).get("status") != "active"
            ):
                return False
            transaction.set(table_ref, table_data)
            if runtime_checkpoint is not None:
                _validate_unowned_table_checkpoint("union", union_id, table_id, runtime_checkpoint)
                transaction.set(
                    state_ref,
                    {
                        "schema_version": 1,
                        "scope_type": "union",
                        "scope_id": union_id,
                        "table_id": table_id,
                        "owner_id": None,
                        "fencing_epoch": 0,
                        "sequence": 0,
                        "checkpoint": runtime_checkpoint,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            return True

        return commit(transaction)

    def close_union_table_record(
        self,
        union_id: str,
        table_id: str,
        *,
        now: datetime | None = None,
    ) -> UnionTableCloseStatus:
        table_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("tables")
            .document(table_id)
        )
        state_ref = self._table_runtime_state_ref("union", union_id, table_id)
        lease_ref = self._table_runtime_lease_ref("union", union_id, table_id)
        transaction = self._client.transaction()
        current_time = now or datetime.now(timezone.utc)

        @firestore.transactional
        def commit(transaction: Any) -> UnionTableCloseStatus:
            table_snapshot = table_ref.get(transaction=transaction)
            if not table_snapshot.exists:
                return UnionTableCloseStatus.NOT_FOUND
            table = table_snapshot.to_dict() or {}
            if table.get("status") == "closed":
                return UnionTableCloseStatus.ALREADY_CLOSED

            state_snapshot = state_ref.get(transaction=transaction)
            if not state_snapshot.exists:
                return UnionTableCloseStatus.CHECKPOINT_MISSING
            state = state_snapshot.to_dict() or {}
            checkpoint = state.get("checkpoint")
            checkpoint_runtime = checkpoint.get("runtime_state") if isinstance(checkpoint, dict) else None
            if (
                not isinstance(checkpoint, dict)
                or checkpoint.get("seats") != []
                or not isinstance(checkpoint_runtime, dict)
                or checkpoint_runtime.get("is_hand_running") is not False
            ):
                return UnionTableCloseStatus.NOT_EMPTY

            lease_snapshot = lease_ref.get(transaction=transaction)
            lease = lease_snapshot.to_dict() or {} if lease_snapshot.exists else {}
            lease_expiry = lease.get("lease_expires_at")
            if (
                lease.get("owner_id") is not None
                and (lease_expiry is None or lease_expiry > current_time)
            ):
                return UnionTableCloseStatus.ACTIVE_LEASE

            next_epoch = max(
                int(state.get("fencing_epoch", 0)),
                int(lease.get("fencing_generation", 0)),
            ) + 1
            transaction.set(
                state_ref,
                {
                    **state,
                    "owner_id": None,
                    "fencing_epoch": next_epoch,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                lease_ref,
                {
                    "scope_type": "union",
                    "scope_id": union_id,
                    "table_id": table_id,
                    "owner_id": None,
                    "fencing_generation": next_epoch,
                    "lease_expires_at": current_time,
                    "heartbeat_at": current_time,
                    "status": "released",
                },
            )
            transaction.set(
                table_ref,
                {"status": "closed", "closed_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            return UnionTableCloseStatus.CLOSED

        return commit(transaction)

    def has_union_table_seats(self, union_id: str, table_id: str) -> bool:
        seat_collection = (
            self._client.collection("unions")
            .document(union_id)
            .collection("tables")
            .document(table_id)
            .collection("seats")
        )
        return next(iter(seat_collection.stream()), None) is not None

    def reserve_union_buy_in(
        self,
        union_id: str,
        table_id: str,
        club_id: str,
        seat_index: int,
        user_id: str,
        username: str,
        buy_in_amount: int,
        buy_in_id: str,
        *,
        runtime_checkpoint: dict[str, Any] | None = None,
        owner_id: str | None = None,
        fencing_epoch: int | None = None,
        expected_sequence: int | None = None,
    ) -> BuyInStatus:
        durable_values = (runtime_checkpoint, owner_id, fencing_epoch, expected_sequence)
        if any(value is not None for value in durable_values) and any(
            value is None for value in durable_values
        ):
            raise ValueError("Checkpoint and lease metadata must be provided together.")
        member_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("members")
            .document(user_id)
        )
        seat_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("tables")
            .document(table_id)
            .collection("seats")
            .document(str(seat_index))
        )
        union_club_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("clubs")
            .document(club_id)
        )
        buy_in_ref = member_ref.collection("transactions").document(buy_in_id)
        club_transaction_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("transactions")
            .document(buy_in_id)
        )
        state_ref = self._table_runtime_state_ref("union", union_id, table_id)
        lease_ref = self._table_runtime_lease_ref("union", union_id, table_id)
        action_key = hashlib.sha256(f"{user_id}\0{buy_in_id}".encode("utf-8")).hexdigest()
        action_ref = state_ref.collection("actions").document(action_key)
        current_time = datetime.now(timezone.utc)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> BuyInStatus:
            member_snapshot = member_ref.get(transaction=transaction)
            seat_snapshot = seat_ref.get(transaction=transaction)
            union_club_snapshot = union_club_ref.get(transaction=transaction)
            buy_in_snapshot = buy_in_ref.get(transaction=transaction)
            club_transaction_snapshot = club_transaction_ref.get(transaction=transaction)
            state_snapshot = state_ref.get(transaction=transaction) if runtime_checkpoint is not None else None
            lease_snapshot = lease_ref.get(transaction=transaction) if runtime_checkpoint is not None else None
            action_snapshot = action_ref.get(transaction=transaction) if runtime_checkpoint is not None else None

            if not union_club_snapshot.exists or (
                union_club_snapshot.to_dict() or {}
            ).get("status") != "active":
                return BuyInStatus.UNION_CLUB_NOT_FOUND
            if buy_in_snapshot.exists:
                buy_in = buy_in_snapshot.to_dict() or {}
                seat = seat_snapshot.to_dict() or {}
                same_request = (
                    buy_in.get("union_id") == union_id
                    and buy_in.get("table_id") == table_id
                    and buy_in.get("seat_index") == seat_index
                    and buy_in.get("amount") == buy_in_amount
                    and seat.get("user_id") == user_id
                    and seat.get("buy_in_id") == buy_in_id
                )
                if runtime_checkpoint is not None:
                    return BuyInStatus.TABLE_STATE_CONFLICT
                return BuyInStatus.ALREADY_RESERVED if same_request else BuyInStatus.IDEMPOTENCY_CONFLICT
            if club_transaction_snapshot.exists:
                return BuyInStatus.IDEMPOTENCY_CONFLICT
            if seat_snapshot.exists:
                return BuyInStatus.SEAT_OCCUPIED
            if not member_snapshot.exists:
                return BuyInStatus.MEMBER_NOT_FOUND

            member = member_snapshot.to_dict() or {}
            balance = member.get("chip_balance", 0)
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
                return BuyInStatus.INVALID_BALANCE
            if balance < buy_in_amount:
                return BuyInStatus.INSUFFICIENT_BALANCE

            if runtime_checkpoint is not None:
                assert owner_id is not None
                assert fencing_epoch is not None
                assert expected_sequence is not None
                assert state_snapshot is not None
                assert lease_snapshot is not None
                assert action_snapshot is not None
                if not state_snapshot.exists or not lease_snapshot.exists or action_snapshot.exists:
                    return BuyInStatus.TABLE_STATE_CONFLICT
                state = state_snapshot.to_dict() or {}
                lease = lease_snapshot.to_dict() or {}
                expires_at = lease.get("lease_expires_at")
                if (
                    state.get("owner_id") != owner_id
                    or state.get("fencing_epoch") != fencing_epoch
                    or state.get("sequence") != expected_sequence
                    or lease.get("owner_id") != owner_id
                    or lease.get("fencing_generation") != fencing_epoch
                    or expires_at is None
                    or expires_at <= current_time
                ):
                    return BuyInStatus.TABLE_STATE_CONFLICT
                payload_hash = table_action_fingerprint(
                    user_id,
                    buy_in_id,
                    {
                        "event": "BUY_IN",
                        "union_id": union_id,
                        "table_id": table_id,
                        "seat_index": seat_index,
                        "amount": buy_in_amount,
                    },
                )
                _validate_table_checkpoint_write(
                    "union",
                    union_id,
                    table_id,
                    owner_id,
                    fencing_epoch,
                    expected_sequence + 1,
                    runtime_checkpoint,
                )

            transaction.update(
                member_ref,
                {
                    "chip_balance": firestore.Increment(-buy_in_amount),
                    "in_play_balance": firestore.Increment(buy_in_amount),
                },
            )
            transaction.set(
                seat_ref,
                {
                    "seat_index": seat_index,
                    "user_id": user_id,
                    "club_id": club_id,
                    "username": username,
                    "stack": buy_in_amount,
                    "buy_in_id": buy_in_id,
                    "status": "sitting_in",
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                buy_in_ref,
                {
                    "transaction_id": buy_in_id,
                    "type": "union_buy_in",
                    "union_id": union_id,
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "amount": buy_in_amount,
                    "chip_balance_delta": -buy_in_amount,
                    "in_play_balance_delta": buy_in_amount,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                club_transaction_ref,
                {
                    "transaction_id": buy_in_id,
                    "currency_type": "CLUB_CHIPS",
                    "type": "BUY_IN",
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "amount": buy_in_amount,
                    "from_user_id": user_id,
                    "to_user_id": None,
                    "performed_by": user_id,
                    "note": "club_table_buy_in",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                },
            )
            if runtime_checkpoint is not None:
                assert owner_id is not None
                assert fencing_epoch is not None
                assert expected_sequence is not None
                transaction.set(
                    state_ref,
                    {
                        **(state_snapshot.to_dict() or {}),
                        "sequence": expected_sequence + 1,
                        "checkpoint": runtime_checkpoint,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    },
                )
                transaction.set(
                    action_ref,
                    {
                        "user_id": user_id,
                        "action_id": buy_in_id,
                        "payload_hash": payload_hash,
                        "sequence": expected_sequence + 1,
                        "fencing_epoch": fencing_epoch,
                        "committed_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            return BuyInStatus.RESERVED

        return commit(transaction)

    def persist_union_hand_history_and_ledgers(
        self,
        union_id: str,
        table_id: str,
        hand_summary: dict[str, Any],
        chip_deltas: dict[str, int],
        player_clubs: dict[str, str],
    ) -> bool:
        union_ref = self._client.collection("unions").document(union_id)
        hand_ref = (
            union_ref.collection("tables")
            .document(table_id)
            .collection("hands")
            .document(hand_summary["hand_id"])
        )
        settlement_date = str(hand_summary["timestamp"])[:10]
        settlement_ref = union_ref.collection("settlements").document(settlement_date)
        settlement_hand_ref = settlement_ref.collection("hands").document(
            hand_summary["hand_id"]
        )
        member_refs = {
            user_id: self._client.collection("clubs")
            .document(player_clubs[user_id])
            .collection("members")
            .document(user_id)
            for user_id in chip_deltas
        }
        seat_refs = {
            str(player["seat"]): union_ref.collection("tables")
            .document(table_id)
            .collection("seats")
            .document(str(player["seat"]))
            for player in hand_summary["players"]
        }
        club_net_deltas: dict[str, int] = {}
        for user_id, delta in chip_deltas.items():
            club_id = player_clubs[user_id]
            club_net_deltas[club_id] = club_net_deltas.get(club_id, 0) + delta
        clearing_refs = {
            club_id: settlement_ref.collection("clubs").document(club_id)
            for club_id in club_net_deltas
        }
        if not self.enqueue_pending_hand(hand_ref, hand_summary):
            return False
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            hand_snapshot = hand_ref.get(transaction=transaction)
            settlement_snapshot = settlement_hand_ref.get(transaction=transaction)
            if not hand_snapshot.exists:
                return False
            if (hand_snapshot.to_dict() or {}).get("settlement_status") != "pending":
                return False
            if settlement_snapshot.exists:
                transaction.set(
                    hand_ref,
                    {
                        "settlement_status": "settled",
                        "settled_at": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )
                return False
            for user_id, delta in chip_deltas.items():
                transaction.set(
                    member_refs[user_id],
                    {"in_play_balance": firestore.Increment(delta)},
                    merge=True,
                )
            for player in hand_summary["players"]:
                transaction.set(
                    seat_refs[str(player["seat"])],
                    {"stack": player["final_stack"]},
                    merge=True,
                )

            club_positions = {}
            for club_id, net_delta in club_net_deltas.items():
                debt = max(-net_delta, 0)
                credit = max(net_delta, 0)
                club_positions[club_id] = {
                    "net_delta": net_delta,
                    "debt": debt,
                    "credit": credit,
                }
                transaction.set(
                    clearing_refs[club_id],
                    {
                        "club_id": club_id,
                        "debt": firestore.Increment(debt),
                        "credit": firestore.Increment(credit),
                    },
                    merge=True,
                )
            transaction.set(
                settlement_hand_ref,
                {
                    "hand_id": hand_summary["hand_id"],
                    "table_id": table_id,
                    "positions": club_positions,
                    "rake_amount": hand_summary.get("rake_amount", 0),
                },
            )
            transaction.set(
                hand_ref,
                {
                    "settlement_status": "settled",
                    "settled_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return True

        return commit(transaction)

    def persist_hand_history_and_ledgers(
        self,
        club_id: str,
        table_id: str,
        hand_summary: dict[str, Any],
        chip_deltas: dict[str, int],
    ) -> bool:
        hand_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("tables")
            .document(table_id)
            .collection("hands")
            .document(hand_summary["hand_id"])
        )
        member_refs = {
            user_id: self._client.collection("clubs").document(club_id)
            .collection("members").document(user_id)
            for user_id in chip_deltas
        }
        pool_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("treasury")
            .document("pools")
        )
        rake_attributions = hand_summary.get("rake_attributions", [])
        rake_stats_refs = {
            str(attribution["user_id"]): self._client.collection("clubs")
            .document(club_id)
            .collection("player_rake_stats")
            .document(str(attribution["user_id"]))
            for attribution in rake_attributions
        }
        rake_hand_refs = {
            str(attribution["user_id"]): rake_stats_refs[
                str(attribution["user_id"])
            ].collection("hands").document(str(hand_summary["hand_id"]))
            for attribution in rake_attributions
        }
        rake_amount = int(hand_summary.get("rake_amount", 0))
        jackpot_amount = int((hand_summary.get("jackpot") or {}).get("fee_collected", 0))
        hotd_amount = 0
        transaction_refs = {
            kind: self._client.collection("clubs").document(club_id)
            .collection("transactions").document(f"{hand_summary['hand_id']}_{kind.lower()}")
            for kind, amount in (
                ("RAKE", rake_amount),
                ("BBJ_DROP", jackpot_amount),
                ("HAND_OF_THE_DAY", hotd_amount),
            )
            if amount > 0
        }
        if not self.enqueue_pending_hand(hand_ref, hand_summary):
            return False
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            hand_snapshot = hand_ref.get(transaction=transaction)
            if not hand_snapshot.exists:
                return False
            if (hand_snapshot.to_dict() or {}).get("settlement_status") != "pending":
                return False
            for user_id, delta in chip_deltas.items():
                transaction.set(
                    member_refs[user_id],
                    {"in_play_balance": firestore.Increment(delta)},
                    merge=True,
                )
            for attribution in rake_attributions:
                user_id = str(attribution["user_id"])
                transaction.set(
                    rake_stats_refs[user_id],
                    {
                        "user_id": user_id,
                        "username": next(
                            (
                                str(player.get("username", ""))
                                for player in hand_summary["players"]
                                if str(player["user_id"]) == user_id
                            ),
                            "",
                        ),
                        "generated_rake": firestore.Increment(
                            int(attribution["generated_rake"])
                        ),
                        "hands_played": firestore.Increment(1),
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    },
                    merge=True,
                )
                transaction.set(
                    rake_hand_refs[user_id],
                    {
                        "hand_id": hand_summary["hand_id"],
                        "table_id": table_id,
                        "user_id": user_id,
                        "eligible_contribution": int(
                            attribution["eligible_contribution"]
                        ),
                        "generated_rake": int(attribution["generated_rake"]),
                        "created_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            for player in hand_summary["players"]:
                seat_ref = (
                    self._client.collection("clubs")
                    .document(club_id)
                    .collection("tables")
                    .document(table_id)
                    .collection("seats")
                    .document(str(player["seat"]))
                )
                transaction.set(
                    seat_ref,
                    {"stack": player["final_stack"]},
                    merge=True,
                )
            pool_updates = {
                "total_rake_collected": rake_amount,
                "bad_beat_jackpot_pool": jackpot_amount,
                "hand_of_the_day_pool": hotd_amount,
            }
            if any(pool_updates.values()):
                transaction.set(
                    pool_ref,
                    {key: firestore.Increment(amount) for key, amount in pool_updates.items()},
                    merge=True,
                )
            for kind, amount in (
                ("RAKE", rake_amount),
                ("BBJ_DROP", jackpot_amount),
                ("HAND_OF_THE_DAY", hotd_amount),
            ):
                if amount <= 0:
                    continue
                transaction.set(
                    transaction_refs[kind],
                    {
                        "transaction_id": f"{hand_summary['hand_id']}_{kind.lower()}",
                        "currency_type": "CLUB_CHIPS",
                        "type": kind,
                        "amount": amount,
                        "from_user_id": None,
                        "to_user_id": None,
                        "performed_by": "system",
                        "hand_id": hand_summary["hand_id"],
                        "timestamp": firestore.SERVER_TIMESTAMP,
                    },
                )
            transaction.set(
                hand_ref,
                {
                    "settlement_status": "settled",
                    "settled_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            return True

        return commit(transaction)


    def reserve_buy_in(
        self,
        club_id: str,
        table_id: str,
        seat_index: int,
        user_id: str,
        username: str,
        buy_in_amount: int,
        buy_in_id: str,
        *,
        runtime_checkpoint: dict[str, Any] | None = None,
        owner_id: str | None = None,
        fencing_epoch: int | None = None,
        expected_sequence: int | None = None,
    ) -> BuyInStatus:
        durable_values = (runtime_checkpoint, owner_id, fencing_epoch, expected_sequence)
        if any(value is not None for value in durable_values) and any(
            value is None for value in durable_values
        ):
            raise ValueError("Checkpoint and lease metadata must be provided together.")
        member_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("members")
            .document(user_id)
        )
        seat_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("tables")
            .document(table_id)
            .collection("seats")
            .document(str(seat_index))
        )
        buy_in_ref = member_ref.collection("transactions").document(buy_in_id)
        club_transaction_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("transactions")
            .document(buy_in_id)
        )
        state_ref = self._table_runtime_state_ref("club", club_id, table_id)
        lease_ref = self._table_runtime_lease_ref("club", club_id, table_id)
        action_key = hashlib.sha256(f"{user_id}\0{buy_in_id}".encode("utf-8")).hexdigest()
        action_ref = state_ref.collection("actions").document(action_key)
        current_time = datetime.now(timezone.utc)
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> BuyInStatus:
            member_snapshot = member_ref.get(transaction=transaction)
            seat_snapshot = seat_ref.get(transaction=transaction)
            buy_in_snapshot = buy_in_ref.get(transaction=transaction)
            club_transaction_snapshot = club_transaction_ref.get(transaction=transaction)
            state_snapshot = state_ref.get(transaction=transaction) if runtime_checkpoint is not None else None
            lease_snapshot = lease_ref.get(transaction=transaction) if runtime_checkpoint is not None else None
            action_snapshot = action_ref.get(transaction=transaction) if runtime_checkpoint is not None else None

            if buy_in_snapshot.exists:
                buy_in = buy_in_snapshot.to_dict() or {}
                seat = seat_snapshot.to_dict() or {}
                same_request = (
                    buy_in.get("table_id") == table_id
                    and buy_in.get("seat_index") == seat_index
                    and buy_in.get("amount") == buy_in_amount
                    and seat.get("user_id") == user_id
                    and seat.get("buy_in_id") == buy_in_id
                )
                if runtime_checkpoint is not None:
                    return BuyInStatus.TABLE_STATE_CONFLICT
                return BuyInStatus.ALREADY_RESERVED if same_request else BuyInStatus.IDEMPOTENCY_CONFLICT
            if club_transaction_snapshot.exists:
                return BuyInStatus.IDEMPOTENCY_CONFLICT

            if seat_snapshot.exists:
                return BuyInStatus.SEAT_OCCUPIED
            if not member_snapshot.exists:
                return BuyInStatus.MEMBER_NOT_FOUND

            member = member_snapshot.to_dict() or {}
            balance = member.get("chip_balance", 0)
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
                return BuyInStatus.INVALID_BALANCE
            if balance < buy_in_amount:
                return BuyInStatus.INSUFFICIENT_BALANCE

            if runtime_checkpoint is not None:
                assert owner_id is not None
                assert fencing_epoch is not None
                assert expected_sequence is not None
                assert state_snapshot is not None
                assert lease_snapshot is not None
                assert action_snapshot is not None
                if not state_snapshot.exists or not lease_snapshot.exists or action_snapshot.exists:
                    return BuyInStatus.TABLE_STATE_CONFLICT
                state = state_snapshot.to_dict() or {}
                lease = lease_snapshot.to_dict() or {}
                expires_at = lease.get("lease_expires_at")
                if (
                    state.get("owner_id") != owner_id
                    or state.get("fencing_epoch") != fencing_epoch
                    or state.get("sequence") != expected_sequence
                    or lease.get("owner_id") != owner_id
                    or lease.get("fencing_generation") != fencing_epoch
                    or expires_at is None
                    or expires_at <= current_time
                ):
                    return BuyInStatus.TABLE_STATE_CONFLICT
                payload_hash = table_action_fingerprint(
                    user_id,
                    buy_in_id,
                    {
                        "event": "BUY_IN",
                        "table_id": table_id,
                        "seat_index": seat_index,
                        "amount": buy_in_amount,
                    },
                )
                _validate_table_checkpoint_write(
                    "club",
                    club_id,
                    table_id,
                    owner_id,
                    fencing_epoch,
                    expected_sequence + 1,
                    runtime_checkpoint,
                )

            transaction.update(
                member_ref,
                {
                    "chip_balance": firestore.Increment(-buy_in_amount),
                    "in_play_balance": firestore.Increment(buy_in_amount),
                },
            )
            transaction.set(
                seat_ref,
                {
                    "seat_index": seat_index,
                    "user_id": user_id,
                    "username": username,
                    "stack": buy_in_amount,
                    "buy_in_id": buy_in_id,
                    "status": "sitting_in",
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                buy_in_ref,
                {
                    "transaction_id": buy_in_id,
                    "type": "buy_in",
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "amount": buy_in_amount,
                    "chip_balance_delta": -buy_in_amount,
                    "in_play_balance_delta": buy_in_amount,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                club_transaction_ref,
                {
                    "transaction_id": buy_in_id,
                    "currency_type": "CLUB_CHIPS",
                    "type": "BUY_IN",
                    "table_id": table_id,
                    "seat_index": seat_index,
                    "amount": buy_in_amount,
                    "from_user_id": user_id,
                    "to_user_id": None,
                    "performed_by": user_id,
                    "note": "club_table_buy_in",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                },
            )
            if runtime_checkpoint is not None:
                assert owner_id is not None
                assert fencing_epoch is not None
                assert expected_sequence is not None
                transaction.set(
                    state_ref,
                    {
                        **(state_snapshot.to_dict() or {}),
                        "sequence": expected_sequence + 1,
                        "checkpoint": runtime_checkpoint,
                        "updated_at": firestore.SERVER_TIMESTAMP,
                    },
                )
                transaction.set(
                    action_ref,
                    {
                        "user_id": user_id,
                        "action_id": buy_in_id,
                        "payload_hash": payload_hash,
                        "sequence": expected_sequence + 1,
                        "fencing_epoch": fencing_epoch,
                        "committed_at": firestore.SERVER_TIMESTAMP,
                    },
                )
            return BuyInStatus.RESERVED

        return commit(transaction)


def _validate_unowned_table_checkpoint(
    scope_type: str,
    scope_id: str,
    table_id: str,
    checkpoint: dict[str, Any],
) -> None:
    if scope_type not in {"club", "union"} or not scope_id or not table_id:
        raise ValueError("Table scope must be club or union and IDs must be non-empty.")
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != 1:
        raise ValueError("Table checkpoint must use schema version 1.")
    table_data = checkpoint.get("table")
    if not isinstance(table_data, dict) or table_data.get("table_id") != table_id:
        raise ValueError("Table checkpoint ID does not match its Firestore path.")


def _validate_table_checkpoint_write(
    scope_type: str,
    scope_id: str,
    table_id: str,
    owner_id: str,
    fencing_epoch: int,
    sequence: int,
    checkpoint: dict[str, Any],
) -> None:
    if scope_type not in {"club", "union"}:
        raise ValueError("Table scope must be club or union.")
    if not scope_id or not table_id or not owner_id:
        raise ValueError("Table scope, ID, and owner ID must be non-empty.")
    if isinstance(fencing_epoch, bool) or not isinstance(fencing_epoch, int) or fencing_epoch < 1:
        raise ValueError("Fencing epoch must be a positive integer.")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("Table sequence must be a non-negative integer.")
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != 1:
        raise ValueError("Table checkpoint must use schema version 1.")
    table_data = checkpoint.get("table")
    if not isinstance(table_data, dict) or table_data.get("table_id") != table_id:
        raise ValueError("Table checkpoint ID does not match its Firestore path.")


def create_hand_repository(enabled: bool) -> NullHandRepository | FirestoreHandRepository:
    if not enabled:
        return NullHandRepository()
    return FirestoreHandRepository(firestore.Client())