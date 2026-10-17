from __future__ import annotations

from typing import Any

import pytest
from google.cloud import firestore

from database import (
    BuyInStatus,
    FirestoreHandRepository,
    TableActionCommitStatus,
    WalletTransactionStatus,
    table_action_fingerprint,
)
from database import hand_repository


class FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

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
            FakeSnapshot(data)
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


def test_create_club_funds_treasury_from_platform_reserve(monkeypatch) -> None:
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

    assert repository.create_club("club-42", "Test Club", "owner-1", "admin-1") is True

    treasury_path = ("clubs", "club-42", "treasury", "wallet")
    initial_grant_path = ("clubs", "club-42", "transactions", "initial-funding")
    reserve_path = ("platform_wallets", "global-playmoney-reserve")
    assert client.documents[treasury_path]["chip_balance"] == 10_000
    assert client.documents[initial_grant_path]["amount"] == 10_000
    assert client.documents[initial_grant_path]["from_wallet_id"] == "global-playmoney-reserve"
    assert client.documents[reserve_path]["chip_balance"] == 990_000


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

    result = repository.commit_table_action(
        "club", "club-1", "table-1", "worker-a", 3, 0,
        "player-1", "action-1", fingerprint, advanced,
    )

    state_path = ("clubs", "club-1", "tables", "table-1", "runtime", "state")
    assert result.status is TableActionCommitStatus.COMMITTED
    assert result.sequence == 1
    assert client.documents[state_path]["checkpoint"] == advanced
    assert client.documents[state_path]["sequence"] == 1

    replay = repository.commit_table_action(
        "club", "club-1", "table-1", "worker-a", 3, 0,
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
    payload_hash = table_action_fingerprint("player-1", "action-1", {"action": "CHECK"})

    committed = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-a", 5, 0,
        "player-1", "action-1", payload_hash, checkpoint,
    )
    conflict = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-a", 5, 0,
        "player-1", "action-1", "0" * 64, checkpoint,
    )
    fenced = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-old", 4, 1,
        "player-1", "action-2", "2" * 64, checkpoint,
    )
    stale_sequence = repository.commit_table_action(
        "union", "union-1", "table-1", "worker-a", 5, 0,
        "player-1", "action-3", "3" * 64, checkpoint,
    )

    assert committed.status is TableActionCommitStatus.COMMITTED
    assert conflict.status is TableActionCommitStatus.IDEMPOTENCY_CONFLICT
    assert fenced.status is TableActionCommitStatus.FENCED
    assert stale_sequence.status is TableActionCommitStatus.SEQUENCE_CONFLICT


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


def test_claim_back_rejects_in_play_balance_and_transfers_available_chips(monkeypatch) -> None:
    monkeypatch.setattr(hand_repository.firestore, "transactional", lambda function: function)
    owner = ("clubs", "club-1", "members", "owner-1")
    active_player = ("clubs", "club-1", "members", "active-player")
    player = ("clubs", "club-1", "members", "player-1")
    client = FakeFirestoreClient(
        {
            owner: {"role": "OWNER", "chip_balance": 200},
            active_player: {"role": "PLAYER", "chip_balance": 500, "in_play_balance": 1},
            player: {"role": "PLAYER", "chip_balance": 500, "in_play_balance": 0},
        }
    )
    repository = FirestoreHandRepository(client)

    assert repository.claim_back_club_chips("club-1", "active-player", "owner-1", 100, "tx-active", "owner-1") == WalletTransactionStatus.PLAYER_HAS_CHIPS_IN_PLAY
    assert repository.claim_back_club_chips("club-1", "player-1", "owner-1", 100, "tx-player", "owner-1") == WalletTransactionStatus.COMPLETED
    assert client.documents[player]["chip_balance"] == 400
    assert client.documents[owner]["chip_balance"] == 300


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
    client = FakeFirestoreClient({})
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
    assert client.documents[("clubs", "new-club", "transactions", "initial-funding")]["amount"] == 10000

    assert not repository.create_club("new-club", "Changed Name", "owner-2", "platform-admin")
    assert client.documents[("clubs", "new-club", "treasury", "wallet")]["chip_balance"] == 10000


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

    assert repository.create_union_table_record("union-1", "table-1", table_data)
    assert not repository.create_union_table_record("union-1", "table-1", table_data)
    assert repository.close_union_table_record("union-1", "table-1")
    assert client.documents[("unions", "union-1", "tables", "table-1")]["status"] == "closed"


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