from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from google.cloud import firestore

from database import (
    BuyInStatus,
    FirestoreRequiredError,
    FirestoreHandRepository,
    RoleChangeStatus,
    TableActionCommitStatus,
    TableLeaseConflictError,
    TableLeaseExpiredError,
    WalletTransactionStatus,
    table_action_fingerprint,
)
from database import hand_repository


class FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None, document_id: str | None = None) -> None:
        self._data = data
        self.exists = data is not None
        self.id = document_id

    def to_dict(self) -> dict[str, Any] | None:
        return dict(self._data) if self._data is not None else None


class FakeQuerySnapshot(FakeSnapshot):
    pass


class FakeDocumentReference:
    def __init__(self, client: FakeFirestoreClient, path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self.client, self.path + (name,))

    def get(self, transaction: FakeTransaction | None = None) -> FakeSnapshot:
        if transaction is not None:
            return transaction.get(self)
        return FakeSnapshot(self.client.documents.get(self.path))


class FakeCollectionReference:
    def __init__(self, client: FakeFirestoreClient, path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def document(self, document_id: str) -> FakeDocumentReference:
        return FakeDocumentReference(self.client, self.path + (document_id,))

    def stream(self) -> list[FakeSnapshot]:
        return [
            FakeSnapshot(data, path[-1])
            for path, data in self.client.documents.items()
            if path[:-1] == self.path
        ]


class FakeCollectionGroupQuery:
    def __init__(
        self,
        client: FakeFirestoreClient,
        collection_name: str,
        field: str,
        expected_value: Any,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.field = field
        self.expected_value = expected_value

    def stream(self) -> list[FakeQuerySnapshot]:
        return [
            FakeQuerySnapshot(data)
            for path, data in self.client.documents.items()
            if len(path) >= 2
            and path[-2] == self.collection_name
            and data.get(self.field) == self.expected_value
        ]


class FakeCollectionGroupReference:
    def __init__(self, client: FakeFirestoreClient, collection_name: str) -> None:
        self.client = client
        self.collection_name = collection_name

    def where(
        self, field: str, operator: str, expected_value: Any
    ) -> FakeCollectionGroupQuery:
        assert operator == "=="
        return FakeCollectionGroupQuery(
            self.client, self.collection_name, field, expected_value
        )


class FakeTransaction:
    def __init__(self, client: FakeFirestoreClient) -> None:
        self.client = client

    def get(self, reference: FakeDocumentReference) -> FakeSnapshot:
        return FakeSnapshot(self.client.documents.get(reference.path))

    def update(self, reference: FakeDocumentReference, values: dict[str, Any]) -> None:
        document = self.client.documents[reference.path]
        for field, value in values.items():
            if isinstance(value, firestore.Increment):
                document[field] = document.get(field, 0) + value.value
            else:
                document[field] = value

    def set(
        self,
        reference: FakeDocumentReference,
        values: dict[str, Any],
        merge: bool = False,
    ) -> None:
        document = dict(self.client.documents.get(reference.path, {})) if merge else {}
        for field, value in values.items():
            if isinstance(value, firestore.Increment):
                document[field] = document.get(field, 0) + value.value
            else:
                document[field] = value
        self.client.documents[reference.path] = document


class FakeFirestoreClient:
    def __init__(self, documents: dict[tuple[str, ...], dict[str, Any]]) -> None:
        self.documents = documents

    def collection(self, name: str) -> FakeCollectionReference:
        return FakeCollectionReference(self, (name,))

    def collection_group(self, name: str) -> FakeCollectionGroupReference:
        return FakeCollectionGroupReference(self, name)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


def test_buy_in_debits_balance_reserves_seat_and_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_path = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient({member_path: {"chip_balance": 1000}})
    repository = FirestoreHandRepository(client)

    arguments = ("club-1", "table-1", 2, "player-1", "Alice", 400, "buy-in-1")
    result = repository.reserve_buy_in(*arguments)

    assert result == BuyInStatus.RESERVED
    assert client.documents[member_path]["chip_balance"] == 600
    assert client.documents[member_path]["in_play_balance"] == 400
    assert client.documents[("clubs", "club-1", "tables", "table-1", "seats", "2")] == {
        "seat_index": 2,
        "user_id": "player-1",
        "username": "Alice",
        "stack": 400,
        "buy_in_id": "buy-in-1",
        "status": "sitting_in",
        "created_at": firestore.SERVER_TIMESTAMP,
    }

    retry_result = repository.reserve_buy_in(*arguments)

    assert retry_result == BuyInStatus.ALREADY_RESERVED
    assert client.documents[member_path]["chip_balance"] == 600


@pytest.mark.parametrize(
    ("actor_role", "current_role", "target_role"),
    [
        ("OWNER", "PLAYER", "AGENT"),
        ("OWNER", "PLAYER", "MANAGER"),
        ("OWNER", "AGENT", "MANAGER"),
        ("OWNER", "MANAGER", "AGENT"),
        ("OWNER", "MANAGER", "PLAYER"),
        ("OWNER", "AGENT", "PLAYER"),
        ("MANAGER", "PLAYER", "MANAGER"),
        ("MANAGER", "AGENT", "MANAGER"),
        ("MANAGER", "MANAGER", "AGENT"),
        ("MANAGER", "MANAGER", "PLAYER"),
        ("MANAGER", "AGENT", "PLAYER"),
    ],
)
def test_club_role_change_allows_policy_transitions(
    monkeypatch,
    actor_role: str,
    current_role: str,
    target_role: str,
) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    club_id = "club-roles"
    client = FakeFirestoreClient({
        ("clubs", club_id): {"owner_user_id": "owner"},
        ("clubs", club_id, "members", "actor"): {"status": "active", "role": actor_role},
        ("clubs", club_id, "members", "target"): {"status": "active", "role": current_role},
    })
    repository = FirestoreHandRepository(client)

    status, previous_role = repository.change_club_member_role(
        club_id, "actor", "target", target_role, "role-change-1"
    )

    assert status is RoleChangeStatus.COMPLETED
    assert previous_role == current_role
    assert client.documents[("clubs", club_id, "members", "target")]["role"] == target_role
    audit = client.documents[("clubs", club_id, "role_changes", "role-change-1")]
    assert audit["from_role"] == current_role
    assert audit["to_role"] == target_role
    assert audit["performed_by"] == "actor"


@pytest.mark.parametrize(
    ("actor_role", "target_user_id", "current_role", "target_role", "expected"),
    [
        ("PLAYER", "target", "PLAYER", "AGENT", RoleChangeStatus.UNAUTHORIZED_ACTOR),
        ("OWNER", "actor", "OWNER", "PLAYER", RoleChangeStatus.SELF_CHANGE),
        ("MANAGER", "target", "PLAYER", "AGENT", RoleChangeStatus.INVALID_TRANSITION),
        ("MANAGER", "owner", "OWNER", "PLAYER", RoleChangeStatus.OWNER_PROTECTED),
    ],
)
def test_club_role_change_rejects_forbidden_transitions(
    monkeypatch,
    actor_role: str,
    target_user_id: str,
    current_role: str,
    target_role: str,
    expected: RoleChangeStatus,
) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    club_id = "club-roles"
    members = {
        "actor": {"status": "active", "role": actor_role},
        "target": {"status": "active", "role": "PLAYER"},
        "owner": {"status": "active", "role": "OWNER"},
    }
    members[target_user_id] = {"status": "active", "role": current_role}
    client = FakeFirestoreClient({
        ("clubs", club_id): {"owner_user_id": "owner"},
        **{
            ("clubs", club_id, "members", user_id): member
            for user_id, member in members.items()
        },
    })
    repository = FirestoreHandRepository(client)

    status, _ = repository.change_club_member_role(
        club_id, "actor", target_user_id, target_role, "role-change-2"
    )

    assert status is expected
    assert ("clubs", club_id, "role_changes", "role-change-2") not in client.documents


def test_club_role_change_is_idempotent_and_detects_conflicting_reuse(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    club_id = "club-roles"
    client = FakeFirestoreClient({
        ("clubs", club_id): {"owner_user_id": "owner"},
        ("clubs", club_id, "members", "owner"): {"status": "active", "role": "OWNER"},
        ("clubs", club_id, "members", "player"): {"status": "active", "role": "PLAYER"},
    })
    repository = FirestoreHandRepository(client)
    arguments = (club_id, "owner", "player", "AGENT", "role-change-3")

    assert repository.change_club_member_role(*arguments) == (
        RoleChangeStatus.COMPLETED,
        "PLAYER",
    )
    assert repository.change_club_member_role(*arguments) == (
        RoleChangeStatus.IDEMPOTENT_REPLAY,
        "PLAYER",
    )
    assert repository.change_club_member_role(
        club_id, "owner", "player", "MANAGER", "role-change-3"
    )[0] is RoleChangeStatus.IDEMPOTENCY_CONFLICT


def test_list_club_members_returns_only_safe_active_member_fields() -> None:
    client = FakeFirestoreClient({
        ("clubs", "club-roles", "members", "owner"): {
            "username": "Club Owner",
            "role": "OWNER",
            "status": "active",
            "chip_balance": 5000,
        },
        ("clubs", "club-roles", "members", "inactive"): {
            "username": "Inactive Member",
            "role": "PLAYER",
            "status": "inactive",
            "chip_balance": 2500,
        },
    })
    repository = FirestoreHandRepository(client)

    assert repository.list_club_members("club-roles") == [
        {
            "user_id": "owner",
            "username": "Club Owner",
            "role": "OWNER",
            "status": "active",
        }
    ]
def test_buy_in_commits_lease_checked_checkpoint_with_balance_and_seat(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    now = datetime.now(timezone.utc)
    member_path = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient({member_path: {"chip_balance": 1000}})
    repository = FirestoreHandRepository(client)
    checkpoint = {
        "schema_version": 1,
        "table": {"table_id": "table-1"},
        "runtime_state": {"seats": [{"seat_index": 2, "user_id": "player-1"}]},
    }
    lease = repository.claim_table_lease(
        "club", "club-1", "table-1", "worker-a", 60,
        initial_checkpoint={"schema_version": 1, "table": {"table_id": "table-1"}},
        now=now,
    )

    status = repository.reserve_buy_in(
        "club-1", "table-1", 2, "player-1", "Alice", 400, "buy-in-durable",
        runtime_checkpoint=checkpoint,
        owner_id="worker-a",
        fencing_epoch=lease["fencing_generation"],
        expected_sequence=0,
    )

    state_path = ("clubs", "club-1", "tables", "table-1", "runtime", "state")
    assert status is BuyInStatus.RESERVED
    assert client.documents[member_path]["chip_balance"] == 600
    assert client.documents[state_path]["sequence"] == 1
    assert client.documents[state_path]["checkpoint"] == checkpoint
    action_paths = [
        path for path in client.documents if path[:-1] == state_path + ("actions",)
    ]
    assert len(action_paths) == 1
    assert len(client.documents[action_paths[0]]["payload_hash"]) == 64


def test_buy_in_checkpoint_conflict_does_not_debit_balance(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    now = datetime.now(timezone.utc)
    member_path = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient({member_path: {"chip_balance": 1000}})
    repository = FirestoreHandRepository(client)
    repository.claim_table_lease(
        "club", "club-1", "table-1", "worker-a", 60,
        initial_checkpoint={"schema_version": 1, "table": {"table_id": "table-1"}},
        now=now,
    )

    status = repository.reserve_buy_in(
        "club-1", "table-1", 2, "player-1", "Alice", 400, "buy-in-stale",
        runtime_checkpoint={"schema_version": 1, "table": {"table_id": "table-1"}},
        owner_id="worker-a",
        fencing_epoch=1,
        expected_sequence=8,
    )

    assert status is BuyInStatus.TABLE_STATE_CONFLICT
    assert client.documents[member_path]["chip_balance"] == 1000
    assert ("clubs", "club-1", "tables", "table-1", "seats", "2") not in client.documents
    assert ("clubs", "club-1", "transactions", "buy-in-stale") not in client.documents


def test_buy_in_expired_lease_does_not_debit_balance(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    now = datetime.now(timezone.utc)
    member_path = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient({member_path: {"chip_balance": 1000}})
    repository = FirestoreHandRepository(client)
    repository.claim_table_lease(
        "club", "club-1", "table-1", "worker-a", 60,
        initial_checkpoint={"schema_version": 1, "table": {"table_id": "table-1"}},
        now=now,
    )
    lease_path = ("clubs", "club-1", "tables", "table-1", "runtime", "lease")
    client.documents[lease_path]["lease_expires_at"] = now - timedelta(seconds=1)

    status = repository.reserve_buy_in(
        "club-1", "table-1", 2, "player-1", "Alice", 400, "buy-in-expired",
        runtime_checkpoint={"schema_version": 1, "table": {"table_id": "table-1"}},
        owner_id="worker-a",
        fencing_epoch=1,
        expected_sequence=0,
    )

    assert status is BuyInStatus.TABLE_STATE_CONFLICT
    assert client.documents[member_path]["chip_balance"] == 1000
    assert ("clubs", "club-1", "tables", "table-1", "seats", "2") not in client.documents


def test_union_buy_in_commits_lease_checked_checkpoint_with_balance_and_seat(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    now = datetime.now(timezone.utc)
    member_path = ("clubs", "club-1", "members", "player-1")
    union_club_path = ("unions", "union-1", "clubs", "club-1")
    client = FakeFirestoreClient({
        member_path: {"chip_balance": 1000},
        union_club_path: {"status": "active"},
    })
    repository = FirestoreHandRepository(client)
    lease = repository.claim_table_lease(
        "union", "union-1", "table-1", "worker-a", 60,
        initial_checkpoint={"schema_version": 1, "table": {"table_id": "table-1"}},
        now=now,
    )
    checkpoint = {
        "schema_version": 1,
        "table": {"table_id": "table-1"},
        "runtime_state": {"seats": [{"seat_index": 1, "user_id": "player-1"}]},
    }

    status = repository.reserve_union_buy_in(
        "union-1", "table-1", "club-1", 1, "player-1", "Alice", 250, "union-buy-in-durable",
        runtime_checkpoint=checkpoint,
        owner_id="worker-a",
        fencing_epoch=lease["fencing_generation"],
        expected_sequence=0,
    )

    state_path = ("unions", "union-1", "tables", "table-1", "runtime", "state")
    assert status is BuyInStatus.RESERVED
    assert client.documents[member_path]["chip_balance"] == 750
    assert client.documents[state_path]["sequence"] == 1
    assert client.documents[state_path]["checkpoint"] == checkpoint


def test_global_playmoney_wallet_grant_is_created_once(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({
        ("platform_wallets", "global-playmoney-reserve"): {
            "wallet_id": "global-playmoney-reserve",
            "wallet_type": "PLATFORM_RESERVE",
            "currency_type": "GLOBAL_PLAYMONEY",
            "chip_balance": 1_000_000,
        }
    })
    repository = FirestoreHandRepository(client)

    wallet, created = repository.ensure_global_wallet("new-user")
    retried_wallet, retried_created = repository.ensure_global_wallet("new-user")

    wallet_path = ("global_wallets", "new-user")
    grant_path = ("global_wallets", "new-user", "transactions", "initial-grant")
    reserve_path = ("platform_wallets", "global-playmoney-reserve")
    assert created is True
    assert wallet["wallet_type"] == "GLOBAL_PLAYMONEY"
    assert wallet["currency_type"] == "GLOBAL_PLAYMONEY"
    assert wallet["chip_balance"] == 20_000
    assert retried_created is False
    assert retried_wallet["chip_balance"] == 20_000
    assert client.documents[wallet_path]["chip_balance"] == 20_000
    assert client.documents[grant_path]["amount"] == 20_000
    assert client.documents[grant_path]["from_wallet_id"] == "global-playmoney-reserve"
    assert client.documents[reserve_path]["chip_balance"] == 980_000


def test_create_club_funds_treasury_from_club_chip_giveaway_reserve(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({
        ("platform_wallets", "club-chip-giveaway-reserve"): {
            "wallet_id": "club-chip-giveaway-reserve",
            "wallet_type": "CLUB_CHIP_GIVEAWAY_RESERVE",
            "currency_type": "CLUB_CHIPS",
            "chip_balance": 1_000_000,
        },
        ("platform_wallets", "global-playmoney-reserve"): {
            "wallet_id": "global-playmoney-reserve",
            "wallet_type": "PLATFORM_RESERVE",
            "currency_type": "GLOBAL_PLAYMONEY",
            "chip_balance": 500_000,
        }
    })
    repository = FirestoreHandRepository(client)

    assert repository.create_club("club-42", "Test Club", "owner-1", "admin-1") is True

    treasury_path = ("clubs", "club-42", "treasury", "wallet")
    initial_grant_path = ("clubs", "club-42", "transactions", "initial-funding")
    reserve_path = ("platform_wallets", "club-chip-giveaway-reserve")
    reserve_grant_path = (
        "platform_wallets",
        "club-chip-giveaway-reserve",
        "transactions",
        "club-club-42-initial-grant",
    )
    assert client.documents[treasury_path]["chip_balance"] == 10_000
    assert client.documents[initial_grant_path]["amount"] == 10_000
    assert client.documents[initial_grant_path]["currency_type"] == "CLUB_CHIPS"
    assert client.documents[initial_grant_path]["from_wallet_id"] == "club-chip-giveaway-reserve"
    assert client.documents[initial_grant_path]["from_wallet_type"] == "CLUB_CHIP_GIVEAWAY_RESERVE"
    assert client.documents[reserve_path]["chip_balance"] == 990_000
    assert client.documents[("platform_wallets", "global-playmoney-reserve")]["chip_balance"] == 500_000
    assert client.documents[reserve_grant_path]["amount"] == 10_000
    assert client.documents[reserve_grant_path]["to_club_id"] == "club-42"
    assert client.documents[reserve_grant_path]["balance_after"] == 990_000


def test_table_checkpoint_initialization_is_create_only(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-1"}}

    created = repository.initialize_table_checkpoint(
        "club", "club-1", "table-1", "worker-a", 1, checkpoint
    )
    retry = repository.initialize_table_checkpoint(
        "club", "club-1", "table-1", "worker-b", 2, checkpoint
    )

    state_path = ("clubs", "club-1", "tables", "table-1", "runtime", "state")
    assert created is True
    assert retry is False
    assert client.documents[state_path]["sequence"] == 0
    assert client.documents[state_path]["owner_id"] == "worker-a"
    assert client.documents[state_path]["fencing_epoch"] == 1
    assert repository.get_table_checkpoint("club", "club-1", "table-1")["checkpoint"] == checkpoint


def test_table_lease_release_rejects_lease_epoch_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client, now_factory=lambda: now)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-release-fence"}}
    assert repository.initialize_table_checkpoint(
        "club", "club-release-fence", "table-release-fence", "worker-a", 1, checkpoint
    )
    lease = repository.claim_table_lease(
        "club", "club-release-fence", "table-release-fence", "worker-a", 30
    )

    lease_path = (
        "clubs", "club-release-fence", "tables", "table-release-fence", "runtime", "lease"
    )
    fencing_generation = lease["fencing_generation"]
    client.documents[lease_path]["fencing_generation"] = fencing_generation + 1
    mismatched_lease = dict(client.documents[lease_path])

    with pytest.raises(TableLeaseExpiredError):
        repository.release_table_lease(
            "club",
            "club-release-fence",
            "table-release-fence",
            "worker-a",
            fencing_generation,
        )

    assert client.documents[lease_path] == mismatched_lease


def test_checkpoint_action_commit_rejects_expired_durable_lease(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client, now_factory=lambda: now)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-expired"}}
    assert repository.initialize_table_checkpoint(
        "club", "club-expired", "table-expired", None, 0, checkpoint
    )
    repository.claim_table_lease(
        "club", "club-expired", "table-expired", "worker-a", 30, now=now
    )
    expired_at = now + timedelta(seconds=31)

    result = repository.commit_table_action(
        "club",
        "club-expired",
        "table-expired",
        "worker-a",
        1,
        0,
        "player-a",
        "action-a",
        "0" * 64,
        checkpoint,
        now=expired_at,
    )

    assert result.status is TableActionCommitStatus.FENCED
def test_table_creation_checkpoint_starts_unowned_then_claims_generation_one(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-created"}}

    assert repository.initialize_table_checkpoint(
        "club", "club-1", "table-created", None, 0, checkpoint
    )
    state = repository.get_table_checkpoint("club", "club-1", "table-created")
    assert state["owner_id"] is None
    assert state["fencing_epoch"] == 0

    lease = repository.claim_table_lease(
        "club", "club-1", "table-created", "worker-a", 30,
        now=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    state = repository.get_table_checkpoint("club", "club-1", "table-created")
    assert lease["fencing_generation"] == 1
    assert state["owner_id"] == "worker-a"
    assert state["fencing_epoch"] == 1
    assert state["checkpoint"] == checkpoint


def test_durable_worker_sessions_expire_and_assignments_come_from_leases(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)

    session = repository.start_worker_session("worker-a", 60)
    assert session["status"] == "active"
    assert repository.start_worker_session("worker-a", 60)["already_active"] is True
    assert repository.worker_session_is_active("worker-a")
    assert repository.heartbeat_worker_session("worker-a", 60)

    session_path = ("worker_sessions", "worker-a")
    client.documents[session_path]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert not repository.worker_session_is_active("worker-a")
    assert not repository.heartbeat_worker_session("worker-a", 60)
    restarted_session = repository.start_worker_session("worker-a", 60)
    assert restarted_session["already_active"] is False

    current_time = datetime.now(timezone.utc)
    client.documents.update(
        {
            ("clubs", "club-1", "tables", "table-active", "runtime", "lease"): {
                "scope_type": "club",
                "scope_id": "club-1",
                "table_id": "table-active",
                "owner_id": "worker-a",
                "fencing_generation": 2,
                "lease_expires_at": current_time + timedelta(seconds=30),
            },
            ("unions", "union-1", "tables", "table-expired", "runtime", "lease"): {
                "scope_type": "union",
                "scope_id": "union-1",
                "table_id": "table-expired",
                "owner_id": "worker-a",
                "fencing_generation": 1,
                "lease_expires_at": current_time - timedelta(seconds=1),
            },
        }
    )

    assignments = repository.list_worker_table_leases("worker-a")
    assert len(assignments) == 1
    assert assignments[0]["table_id"] == "table-active"
    assert assignments[0]["scope_type"] == "club"

    assert repository.stop_worker_session("worker-a")
    assert not repository.worker_session_is_active("worker-a")


def test_table_lease_is_durable_fenced_and_reclaimable(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    initial = datetime(2026, 10, 17, tzinfo=timezone.utc)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-lease"}}
    assert repository.initialize_table_checkpoint(
        "club", "club-1", "table-lease", "worker-a", 1, checkpoint
    )

    first = repository.claim_table_lease(
        "club", "club-1", "table-lease", "worker-a", 30, now=initial
    )
    with pytest.raises(TableLeaseConflictError):
        repository.claim_table_lease(
            "club", "club-1", "table-lease", "worker-b", 30,
            now=initial + timedelta(seconds=1),
        )

    second = repository.claim_table_lease(
        "club", "club-1", "table-lease", "worker-b", 30,
        now=initial + timedelta(seconds=31),
    )
    state = repository.get_table_checkpoint("club", "club-1", "table-lease")
    assert first["fencing_generation"] == 2
    assert second["fencing_generation"] == 3
    assert state["owner_id"] == "worker-b"
    assert state["fencing_epoch"] == 3

    with pytest.raises(TableLeaseConflictError):
        repository.authorize_table_lease(
            "club", "club-1", "table-lease", "worker-a", 2,
            now=initial + timedelta(seconds=32),
        )
    with pytest.raises(TableLeaseExpiredError):
        repository.authorize_table_lease(
            "club", "club-1", "table-lease", "worker-b", 3,
            now=initial + timedelta(seconds=62),
        )

    renewed = repository.renew_table_lease(
        "club", "club-1", "table-lease", "worker-b", 3, 30,
        now=initial + timedelta(seconds=32),
    )
    assert renewed["lease_expires_at"] == initial + timedelta(seconds=62)
    assert repository.release_table_lease(
        "club", "club-1", "table-lease", "worker-b", 3,
        now=initial + timedelta(seconds=33),
    ) is True

    third = repository.claim_table_lease(
        "club", "club-1", "table-lease", "worker-c", 30,
        now=initial + timedelta(seconds=34),
    )
    assert third["fencing_generation"] == 4


def test_table_lease_claim_advances_checkpoint_epoch_when_lease_record_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-missing-lease"}}
    assert repository.initialize_table_checkpoint(
        "club", "club-1", "table-missing-lease", "old-worker", 7, checkpoint
    )

    claimed = repository.claim_table_lease(
        "club", "club-1", "table-missing-lease", "new-worker", 30
    )

    state = repository.get_table_checkpoint("club", "club-1", "table-missing-lease")
    assert claimed["fencing_generation"] == 8
    assert state["fencing_epoch"] == 8


def test_table_action_commit_atomically_advances_checkpoint_and_replays(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    initial = {"schema_version": 1, "table": {"table_id": "table-1"}, "state": "before"}
    advanced = {"schema_version": 1, "table": {"table_id": "table-1"}, "state": "after"}
    action = {"action": "CALL", "amount": None}
    fingerprint = table_action_fingerprint("player-1", "action-1", action)
    assert repository.initialize_table_checkpoint(
        "club", "club-1", "table-1", "worker-a", 3, initial
    )
    lease = repository.claim_table_lease("club", "club-1", "table-1", "worker-a", 30)
    fencing_generation = lease["fencing_generation"]

    result = repository.commit_table_action(
        "club", "club-1", "table-1", "worker-a", fencing_generation, 0,
        "player-1", "action-1", fingerprint, advanced,
    )

    state_path = ("clubs", "club-1", "tables", "table-1", "runtime", "state")
    assert result.status is TableActionCommitStatus.COMMITTED
    assert result.sequence == 1
    assert client.documents[state_path]["checkpoint"] == advanced
    assert client.documents[state_path]["sequence"] == 1

    replay = repository.commit_table_action(
        "club", "club-1", "table-1", "worker-a", fencing_generation, 0,
        "player-1", "action-1", fingerprint, initial,
    )
    assert replay.status is TableActionCommitStatus.IDEMPOTENT_REPLAY
    assert replay.sequence == 1
    assert client.documents[state_path]["checkpoint"] == advanced


def test_table_action_commit_rejects_payload_conflict_stale_epoch_and_sequence(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-1"}}
    assert repository.initialize_table_checkpoint(
        "union", "union-1", "table-1", "worker-a", 5, checkpoint
    )
    lease = repository.claim_table_lease("union", "union-1", "table-1", "worker-a", 30)
    fencing_generation = lease["fencing_generation"]
    payload_hash = table_action_fingerprint("player-1", "action-1", {"action": "CHECK"})

    committed = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-a", fencing_generation, 0,
        "player-1", "action-1", payload_hash, checkpoint,
    )
    conflict = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-a", fencing_generation, 0,
        "player-1", "action-1", "0" * 64, checkpoint,
    )
    fenced = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-old", 4, 1,
        "player-1", "action-2", "2" * 64, checkpoint,
    )
    stale_sequence = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-a", fencing_generation, 0,
        "player-1", "action-3", "3" * 64, checkpoint,
    )

    assert committed.status is TableActionCommitStatus.COMMITTED
    assert conflict.status is TableActionCommitStatus.IDEMPOTENCY_CONFLICT
    assert fenced.status is TableActionCommitStatus.FENCED
    assert stale_sequence.status is TableActionCommitStatus.SEQUENCE_CONFLICT


def test_table_action_commit_rejects_expired_lease_in_transaction(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    initial = datetime(2026, 10, 17, tzinfo=timezone.utc)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-expired-action"}}
    assert repository.initialize_table_checkpoint(
        "club", "club-1", "table-expired-action", "worker-a", 1, checkpoint
    )
    lease = repository.claim_table_lease(
        "club", "club-1", "table-expired-action", "worker-a", 1, now=initial
    )
    payload_hash = table_action_fingerprint("player-1", "action-expired", {"action": "CHECK"})

    result = repository.commit_table_action(
        "club", "club-1", "table-expired-action", "worker-a",
        lease["fencing_generation"], 0, "player-1", "action-expired", payload_hash,
        checkpoint, now=initial + timedelta(seconds=2),
    )

    state = repository.get_table_checkpoint("club", "club-1", "table-expired-action")
    assert result.status is TableActionCommitStatus.FENCED
    assert state["sequence"] == 0


def test_table_action_commit_rejects_invalid_fingerprint_without_writing(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-1"}}
    repository.initialize_table_checkpoint(
        "club", "club-1", "table-1", "worker-a", 1, checkpoint
    )

    with pytest.raises(ValueError, match="SHA-256 payload hash"):
        repository.commit_table_action(
            "club", "club-1", "table-1", "worker-a", 1, 0,
            "player-1", "action-1", "not-a-hash", checkpoint,
        )


def test_buy_in_does_not_write_when_balance_is_insufficient(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_path = ("clubs", "club-1", "members", "player-1")
    initial_documents = {member_path: {"chip_balance": 100}}
    client = FakeFirestoreClient({path: dict(data) for path, data in initial_documents.items()})
    repository = FirestoreHandRepository(client)

    result = repository.reserve_buy_in(
        "club-1", "table-1", 2, "player-1", "Alice", 400, "buy-in-2"
    )

    assert result == BuyInStatus.INSUFFICIENT_BALANCE
    assert client.documents == initial_documents


def test_buy_in_rejects_transaction_id_owned_by_another_club_ledger_entry(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_path = ("clubs", "club-1", "members", "player-1")
    ledger_path = ("clubs", "club-1", "transactions", "buy-in-reused")
    client = FakeFirestoreClient(
        {
            member_path: {"chip_balance": 1000},
            ledger_path: {"type": "SEND_OUT", "amount": 50},
        }
    )
    repository = FirestoreHandRepository(client)

    result = repository.reserve_buy_in(
        "club-1", "table-1", 2, "player-1", "Alice", 400, "buy-in-reused"
    )

    assert result == BuyInStatus.IDEMPOTENCY_CONFLICT
    assert client.documents[member_path]["chip_balance"] == 1000


def test_hand_settlement_updates_in_play_balance_not_wallet(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_path = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient(
        {member_path: {"chip_balance": 600, "in_play_balance": 400}}
    )
    repository = FirestoreHandRepository(client)
    hand_summary = {
        "hand_id": "hand-1",
        "table_id": "table-1",
        "players": [
            {"seat": 2, "user_id": "player-1", "final_stack": 450, "net_delta": 50}
        ],
    }

    persisted = repository.persist_hand_history_and_ledgers(
        "club-1", "table-1", hand_summary, {"player-1": 50}
    )

    assert persisted
    assert client.documents[member_path] == {
        "chip_balance": 600,
        "in_play_balance": 450,
    }
    assert client.documents[("clubs", "club-1", "tables", "table-1", "seats", "2")] == {
        "stack": 450
    }


def test_hand_history_lookup_is_scoped_to_club() -> None:
    hand_one = {"club_id": "club-1", "hand_id": "same-id", "frames": []}
    hand_two = {"club_id": "club-2", "hand_id": "same-id", "frames": []}
    client = FakeFirestoreClient(
        {
            ("clubs", "club-1", "tables", "table-1", "hands", "same-id"): hand_one,
            ("clubs", "club-2", "tables", "table-1", "hands", "same-id"): hand_two,
        }
    )
    repository = FirestoreHandRepository(client)

    assert repository.get_hand_history("club-1", "same-id") == hand_one
    assert repository.get_hand_history("club-3", "same-id") is None


def test_send_out_is_double_entry_idempotent_and_limited_to_agent_downline(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    sender = ("clubs", "club-1", "members", "agent-1")
    player = ("clubs", "club-1", "members", "player-1")
    other_player = ("clubs", "club-1", "members", "player-2")
    client = FakeFirestoreClient(
        {
            sender: {"role": "AGENT", "chip_balance": 1000},
            player: {"role": "PLAYER", "agent_id": "agent-1", "chip_balance": 100},
            other_player: {"role": "PLAYER", "agent_id": "agent-2", "chip_balance": 100},
        }
    )
    repository = FirestoreHandRepository(client)

    assert repository.send_out_club_chips("club-1", "agent-1", "player-1", 250, "tx-1", "agent-1") == WalletTransactionStatus.COMPLETED
    assert client.documents[sender]["chip_balance"] == 750
    assert client.documents[player]["chip_balance"] == 350
    assert repository.send_out_club_chips("club-1", "agent-1", "player-1", 250, "tx-1", "agent-1") == WalletTransactionStatus.IDEMPOTENT_REPLAY
    assert repository.send_out_club_chips("club-1", "agent-1", "player-2", 250, "tx-2", "agent-1") == WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
    assert client.documents[sender]["chip_balance"] == 750


def test_claim_back_transfers_only_available_chips_with_balance_in_play(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    owner = ("clubs", "club-1", "members", "owner-1")
    active_player = ("clubs", "club-1", "members", "active-player")
    player = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient(
        {
            owner: {"role": "OWNER", "chip_balance": 200},
            active_player: {"role": "PLAYER", "chip_balance": 500, "in_play_balance": 300},
            player: {"role": "PLAYER", "chip_balance": 500, "in_play_balance": 0},
        }
    )
    repository = FirestoreHandRepository(client)

    assert repository.claim_back_club_chips("club-1", "active-player", "owner-1", 100, "tx-active", "owner-1") == WalletTransactionStatus.COMPLETED
    assert client.documents[active_player]["chip_balance"] == 400
    assert client.documents[active_player]["in_play_balance"] == 300
    assert client.documents[owner]["chip_balance"] == 300
    assert repository.claim_back_club_chips("club-1", "player-1", "owner-1", 100, "tx-player", "owner-1") == WalletTransactionStatus.COMPLETED
    assert client.documents[player]["chip_balance"] == 400
    assert client.documents[owner]["chip_balance"] == 400


def test_inter_club_treasury_transfer_is_atomic_and_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    source_wallet = ("clubs", "club-1", "treasury", "wallet")
    destination_wallet = ("clubs", "club-2", "treasury", "wallet")
    client = FakeFirestoreClient(
        {
            ("clubs", "club-1"): {"name": "Club One"},
            ("clubs", "club-2"): {"name": "Club Two"},
            ("clubs", "club-1", "members", "manager-1"): {"role": "MANAGER", "status": "active"},
            source_wallet: {"chip_balance": 900},
            destination_wallet: {"chip_balance": 100},
        }
    )
    repository = FirestoreHandRepository(client)

    result = repository.transfer_between_club_wallets(
        "club-1", "club-2", "manager-1", 250, "transfer-1", "manager-1", "club funding"
    )

    assert result == WalletTransactionStatus.COMPLETED
    assert client.documents[source_wallet]["chip_balance"] == 650
    assert client.documents[destination_wallet]["chip_balance"] == 350
    assert client.documents[("clubs", "club-1", "transactions", "transfer-1")]["type"] == "INTER_CLUB_SEND_OUT"
    assert client.documents[("clubs", "club-2", "transactions", "transfer-1")]["type"] == "INTER_CLUB_RECEIVE"

    retry = repository.transfer_between_club_wallets(
        "club-1", "club-2", "manager-1", 250, "transfer-1", "manager-1", "club funding"
    )

    assert retry == WalletTransactionStatus.IDEMPOTENT_REPLAY
    assert client.documents[source_wallet]["chip_balance"] == 650
    assert client.documents[destination_wallet]["chip_balance"] == 350


def test_inter_club_treasury_transfer_rejects_unauthorized_and_insufficient_funds(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    source_wallet = ("clubs", "club-1", "treasury", "wallet")
    destination_wallet = ("clubs", "club-2", "treasury", "wallet")
    client = FakeFirestoreClient(
        {
            ("clubs", "club-1"): {},
            ("clubs", "club-2"): {},
            ("clubs", "club-1", "members", "agent-1"): {"role": "AGENT", "status": "active"},
            ("clubs", "club-1", "members", "owner-1"): {"role": "OWNER", "status": "active"},
            source_wallet: {"chip_balance": 100},
            destination_wallet: {"chip_balance": 50},
        }
    )
    repository = FirestoreHandRepository(client)

    unauthorized = repository.transfer_between_club_wallets(
        "club-1", "club-2", "agent-1", 10, "transfer-agent", "agent-1"
    )
    insufficient = repository.transfer_between_club_wallets(
        "club-1", "club-2", "owner-1", 101, "transfer-overdraw", "owner-1"
    )

    assert unauthorized == WalletTransactionStatus.UNAUTHORIZED_OPERATOR
    assert insufficient == WalletTransactionStatus.INSUFFICIENT_BALANCE
    assert client.documents[source_wallet]["chip_balance"] == 100
    assert client.documents[destination_wallet]["chip_balance"] == 50
    assert ("clubs", "club-1", "transactions", "transfer-agent") not in client.documents
    assert ("clubs", "club-1", "transactions", "transfer-overdraw") not in client.documents


def test_create_club_grants_initial_treasury_funds_once(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({
        ("platform_wallets", "club-chip-giveaway-reserve"): {
            "wallet_id": "club-chip-giveaway-reserve",
            "wallet_type": "CLUB_CHIP_GIVEAWAY_RESERVE",
            "currency_type": "CLUB_CHIPS",
            "chip_balance": hand_repository.INITIAL_CLUB_CHIP_GIVEAWAY_RESERVE_BALANCE,
        }
    })
    repository = FirestoreHandRepository(client)

    assert repository.create_club("new-club", "New Club", "owner-1", "platform-admin")

    assert client.documents[("clubs", "new-club")]["owner_user_id"] == "owner-1"
    assert client.documents[("clubs", "new-club", "members", "owner-1")] == {
        "user_id": "owner-1",
        "role": "OWNER",
        "status": "active",
        "chip_balance": 0,
        "in_play_balance": 0,
        "created_at": firestore.SERVER_TIMESTAMP,
    }
    assert client.documents[("clubs", "new-club", "treasury", "wallet")] == {
        "chip_balance": 10000
    }
    initial_funding = client.documents[("clubs", "new-club", "transactions", "initial-funding")]
    assert initial_funding["amount"] == 10000
    assert initial_funding["from_wallet_id"] == "club-chip-giveaway-reserve"
    assert client.documents[("platform_wallets", "club-chip-giveaway-reserve")]["chip_balance"] == (
        hand_repository.INITIAL_CLUB_CHIP_GIVEAWAY_RESERVE_BALANCE - 10000
    )
    assert client.documents[(
        "platform_wallets",
        "club-chip-giveaway-reserve",
        "transactions",
        "club-new-club-initial-grant",
    )]["amount"] == 10000

    assert not repository.create_club("new-club", "Changed Name", "owner-2", "platform-admin")
    assert client.documents[("clubs", "new-club", "treasury", "wallet")]["chip_balance"] == 10000


def test_create_club_requires_preprovisioned_giveaway_reserve(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)

    with pytest.raises(FirestoreRequiredError, match="reserve is not provisioned"):
        repository.create_club("club-no-reserve", "No Reserve", "owner-1", "admin-1")

    assert ("clubs", "club-no-reserve") not in client.documents
    assert not any(path[0] == "platform_wallets" for path in client.documents)


def test_hand_settlement_increments_club_pools_once(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_path = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient(
        {member_path: {"chip_balance": 0, "in_play_balance": 100}}
    )
    repository = FirestoreHandRepository(client)
    summary = {
        "hand_id": "hand-financial-1",
        "table_id": "table-1",
        "rake_amount": 5,
        "jackpot": {"fee_collected": 3},
        "hand_of_the_day": {"prize_amount": 2},
        "players": [{"seat": 0, "user_id": "player-1", "final_stack": 100}],
    }

    assert repository.persist_hand_history_and_ledgers("club-1", "table-1", summary, {"player-1": 0})
    assert not repository.persist_hand_history_and_ledgers("club-1", "table-1", summary, {"player-1": 0})
    assert client.documents[("clubs", "club-1", "treasury", "pools")] == {
        "total_rake_collected": 5,
        "bad_beat_jackpot_pool": 3,
        "hand_of_the_day_pool": 0,
    }


def test_pending_hand_settlement_recovers_and_applies_once(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_path = ("clubs", "club-1", "members", "player-1")
    hand_path = ("clubs", "club-1", "tables", "table-1", "hands", "hand-retry")
    client = FakeFirestoreClient(
        {member_path: {"chip_balance": 100, "in_play_balance": 100}}
    )
    repository = FirestoreHandRepository(client)
    summary = {
        "hand_id": "hand-retry",
        "club_id": "club-1",
        "table_id": "table-1",
        "timestamp": "2026-10-15T00:00:00+00:00",
        "rake_amount": 4,
        "jackpot": {"fee_collected": 2},
        "players": [
            {
                "seat": 0,
                "user_id": "player-1",
                "final_stack": 94,
                "net_delta": -6,
            }
        ],
    }
    hand_ref = (
        client.collection("clubs")
        .document("club-1")
        .collection("tables")
        .document("table-1")
        .collection("hands")
        .document("hand-retry")
    )

    assert repository.enqueue_pending_hand(hand_ref, summary)
    assert client.documents[hand_path]["settlement_status"] == "pending"
    assert client.documents[member_path]["in_play_balance"] == 100

    assert repository.recover_pending_settlements() == 1
    assert client.documents[member_path]["in_play_balance"] == 94
    assert client.documents[hand_path]["settlement_status"] == "settled"
    assert repository.recover_pending_settlements() == 0
    assert client.documents[member_path]["in_play_balance"] == 94
    assert client.documents[("clubs", "club-1", "treasury", "pools")]["total_rake_collected"] == 4


def test_union_membership_lookup_and_club_union_discovery() -> None:
    client = FakeFirestoreClient(
        {
            ("unions", "union-1", "admins", "admin-1"): {
                "status": "active",
                "role": "UNION_ADMIN",
            },
            ("unions", "union-1", "clubs", "club-1"): {
                "union_id": "union-1",
                "club_id": "club-1",
                "union_name": "Pinoy Poker Alliance",
                "status": "active",
            },
            ("unions", "union-2", "clubs", "club-1"): {
                "union_id": "union-2",
                "club_id": "club-1",
                "union_name": "Inactive Union",
                "status": "inactive",
            },
        }
    )
    repository = FirestoreHandRepository(client)

    assert repository.get_union_admin("union-1", "admin-1")["role"] == "UNION_ADMIN"
    assert repository.is_club_in_union("union-1", "club-1")
    assert not repository.is_club_in_union("union-2", "club-1")
    assert repository.get_unions_for_club("club-1") == [
        {"union_id": "union-1", "name": "Pinoy Poker Alliance"}
    ]


def test_union_buy_ins_and_interclub_settlement_are_atomic_and_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    member_a = ("clubs", "club-a", "members", "player-a")
    member_b = ("clubs", "club-b", "members", "player-b")
    client = FakeFirestoreClient(
        {
            member_a: {"chip_balance": 1000, "in_play_balance": 0},
            member_b: {"chip_balance": 1000, "in_play_balance": 0},
            ("unions", "union-1", "clubs", "club-a"): {"status": "active"},
            ("unions", "union-1", "clubs", "club-b"): {"status": "active"},
        }
    )
    repository = FirestoreHandRepository(client)

    for seat, club_id, user_id in ((0, "club-a", "player-a"), (1, "club-b", "player-b")):
        result = repository.reserve_union_buy_in(
            "union-1",
            "table-1",
            club_id,
            seat,
            user_id,
            user_id,
            100,
            f"union-buy-in-{seat}",
        )
        assert result == BuyInStatus.RESERVED

    hand_summary = {
        "hand_id": "union-hand-1",
        "union_id": "union-1",
        "table_id": "table-1",
        "timestamp": "2026-10-14T10:00:00+00:00",
        "rake_amount": 0,
        "players": [
            {"seat": 0, "user_id": "player-a", "final_stack": 50, "net_delta": -50},
            {"seat": 1, "user_id": "player-b", "final_stack": 150, "net_delta": 50},
        ],
    }
    deltas = {"player-a": -50, "player-b": 50}
    player_clubs = {"player-a": "club-a", "player-b": "club-b"}

    assert repository.persist_union_hand_history_and_ledgers(
        "union-1", "table-1", hand_summary, deltas, player_clubs
    )
    assert client.documents[member_a]["in_play_balance"] == 50
    assert client.documents[member_b]["in_play_balance"] == 150
    assert client.documents[("unions", "union-1", "settlements", "2026-10-14", "clubs", "club-a")] == {
        "club_id": "club-a",
        "debt": 50,
        "credit": 0,
    }
    assert client.documents[("unions", "union-1", "settlements", "2026-10-14", "clubs", "club-b")] == {
        "club_id": "club-b",
        "debt": 0,
        "credit": 50,
    }

    assert not repository.persist_union_hand_history_and_ledgers(
        "union-1", "table-1", hand_summary, deltas, player_clubs
    )
    assert client.documents[member_a]["in_play_balance"] == 50
    assert client.documents[member_b]["in_play_balance"] == 150


def test_union_table_metadata_is_created_once_and_soft_closed(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    client = FakeFirestoreClient({})
    repository = FirestoreHandRepository(client)
    table_data = {
        "union_id": "union-1",
        "table_id": "table-1",
        "table_name": "Union Main",
        "origin_club_id": "club-a",
        "status": "open",
    }
    checkpoint = {"schema_version": 1, "table": {"table_id": "table-1"}}

    assert repository.create_union_table_record(
        "union-1", "table-1", table_data, runtime_checkpoint=checkpoint
    )
    assert not repository.create_union_table_record(
        "union-1", "table-1", table_data, runtime_checkpoint=checkpoint
    )
    assert repository.close_union_table_record("union-1", "table-1")
    assert client.documents[("unions", "union-1", "tables", "table-1")]["status"] == "closed"
    state_path = ("unions", "union-1", "tables", "table-1", "runtime", "state")
    assert client.documents[state_path]["owner_id"] is None
    assert client.documents[state_path]["fencing_epoch"] == 0
    assert client.documents[state_path]["checkpoint"] == checkpoint


def test_union_table_with_persisted_seat_cannot_be_treated_as_empty() -> None:
    client = FakeFirestoreClient(
        {
            ("unions", "union-1", "tables", "table-1", "seats", "0"): {
                "club_id": "club-a",
                "user_id": "player-a",
                "stack": 500,
            }
        }
    )
    repository = FirestoreHandRepository(client)

    assert repository.has_union_table_seats("union-1", "table-1")
    assert not repository.has_union_table_seats("union-1", "table-2")