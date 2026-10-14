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


class FirestoreRequiredError(RuntimeError):
    pass


class NullHandRepository:
    def get_club_member(self, club_id: str, user_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to verify club membership.")

    def get_hand_history(self, club_id: str, hand_id: str) -> dict[str, Any] | None:
        raise FirestoreRequiredError("Firestore must be enabled to load hand history.")

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