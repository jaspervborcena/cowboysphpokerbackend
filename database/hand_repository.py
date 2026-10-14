from __future__ import annotations

from enum import StrEnum
from typing import Any

from google.cloud import firestore


class BuyInStatus(StrEnum):
    RESERVED = "reserved"
    ALREADY_RESERVED = "already_reserved"
    MEMBER_NOT_FOUND = "member_not_found"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    INVALID_BALANCE = "invalid_balance"
    SEAT_OCCUPIED = "seat_occupied"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNION_CLUB_NOT_FOUND = "union_club_not_found"


class FirestoreRequiredError(RuntimeError):
    pass


class NullHandRepository:
    def get_club_member(self, club_id: str, user_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to verify club membership.")

    def get_hand_history(self, club_id: str, hand_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load hand history.")

    def get_union_hand_history(self, union_id: str, hand_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load union hand history.")

    def get_union_admin(self, union_id: str, user_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to verify union membership.")

    def is_club_in_union(self, union_id: str, club_id: str) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to verify union club membership.")

    def get_unions_for_club(self, club_id: str) -> list[dict[str, str]]:
        raise FirestoreRequiredError("Firestore must be enabled to load club unions.")

    def create_union_table_record(
        self, union_id: str, table_id: str, table_data: dict[str, Any]
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to create a union table.")

    def close_union_table_record(self, union_id: str, table_id: str) -> bool:
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
    ) -> BuyInStatus:
        raise FirestoreRequiredError("Firestore must be enabled to reserve a seat.")


class FirestoreHandRepository:
    def __init__(self, client: firestore.Client) -> None:
        self._client = client

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
            }
            for snapshot in snapshots
            if (data := snapshot.to_dict() or {}).get("status") == "active"
            and data.get("union_id")
        ]
        return sorted(unions, key=lambda union: union["union_id"])

    def create_union_table_record(
        self, union_id: str, table_id: str, table_data: dict[str, Any]
    ) -> bool:
        table_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("tables")
            .document(table_id)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            if table_ref.get(transaction=transaction).exists:
                return False
            transaction.set(table_ref, table_data)
            return True

        return commit(transaction)

    def close_union_table_record(self, union_id: str, table_id: str) -> bool:
        table_ref = (
            self._client.collection("unions")
            .document(union_id)
            .collection("tables")
            .document(table_id)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            if not table_ref.get(transaction=transaction).exists:
                return False
            transaction.set(
                table_ref,
                {"status": "closed", "closed_at": firestore.SERVER_TIMESTAMP},
                merge=True,
            )
            return True

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
    ) -> BuyInStatus:
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
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> BuyInStatus:
            member_snapshot = member_ref.get(transaction=transaction)
            seat_snapshot = seat_ref.get(transaction=transaction)
            union_club_snapshot = union_club_ref.get(transaction=transaction)
            buy_in_snapshot = buy_in_ref.get(transaction=transaction)

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
                return BuyInStatus.ALREADY_RESERVED if same_request else BuyInStatus.IDEMPOTENCY_CONFLICT
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
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            if hand_ref.get(transaction=transaction).exists or settlement_hand_ref.get(
                transaction=transaction
            ).exists:
                return False

            transaction.set(hand_ref, hand_summary)
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
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            if hand_ref.get(transaction=transaction).exists:
                return False
            transaction.set(hand_ref, hand_summary)
            for user_id, delta in chip_deltas.items():
                transaction.set(
                    member_refs[user_id],
                    {"in_play_balance": firestore.Increment(delta)},
                    merge=True,
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
    ) -> BuyInStatus:
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
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> BuyInStatus:
            member_snapshot = member_ref.get(transaction=transaction)
            seat_snapshot = seat_ref.get(transaction=transaction)
            buy_in_snapshot = buy_in_ref.get(transaction=transaction)

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
                return BuyInStatus.ALREADY_RESERVED if same_request else BuyInStatus.IDEMPOTENCY_CONFLICT

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
            return BuyInStatus.RESERVED

        return commit(transaction)


def create_hand_repository(enabled: bool) -> NullHandRepository | FirestoreHandRepository:
    if not enabled:
        return NullHandRepository()
    return FirestoreHandRepository(firestore.Client())