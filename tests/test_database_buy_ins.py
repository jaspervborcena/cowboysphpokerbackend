from __future__ import annotations

from typing import Any

from google.cloud import firestore

from database import BuyInStatus, FirestoreHandRepository
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