from __future__ import annotations

from enum import StrEnum
from typing import Any

from google.cloud import firestore


INITIAL_CLUB_TREASURY_FUNDS = 10_000


class BuyInStatus(StrEnum):
    RESERVED = "reserved"
    ALREADY_RESERVED = "already_reserved"
    MEMBER_NOT_FOUND = "member_not_found"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    INVALID_BALANCE = "invalid_balance"
    SEAT_OCCUPIED = "seat_occupied"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    UNION_CLUB_NOT_FOUND = "union_club_not_found"


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


class FirestoreRequiredError(RuntimeError):
    pass


class NullHandRepository:
    def recover_pending_settlements(self) -> int:
        return 0

    def get_club_wallet_balance(self, club_id: str) -> int:
        raise FirestoreRequiredError("Firestore must be enabled to load the club wallet.")

    def create_club(
        self,
        club_id: str,
        name: str,
        owner_user_id: str,
        created_by: str,
    ) -> bool:
        raise FirestoreRequiredError("Firestore must be enabled to create a club.")

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

    def create_club(
        self,
        club_id: str,
        name: str,
        owner_user_id: str,
        created_by: str,
    ) -> bool:
        club_ref = self._client.collection("clubs").document(club_id)
        owner_ref = club_ref.collection("members").document(owner_user_id)
        wallet_ref = club_ref.collection("treasury").document("wallet")
        initial_funding_ref = club_ref.collection("transactions").document("initial-funding")
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> bool:
            club_snapshot = club_ref.get(transaction=transaction)
            if club_snapshot.exists:
                return False

            transaction.set(
                club_ref,
                {
                    "club_id": club_id,
                    "name": name,
                    "status": "active",
                    "owner_user_id": owner_user_id,
                    "created_by": created_by,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                owner_ref,
                {
                    "user_id": owner_user_id,
                    "role": "OWNER",
                    "status": "active",
                    "chip_balance": 0,
                    "in_play_balance": 0,
                    "created_at": firestore.SERVER_TIMESTAMP,
                },
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
                    "to_club_id": club_id,
                    "amount": INITIAL_CLUB_TREASURY_FUNDS,
                    "performed_by": created_by,
                    "note": "Initial club treasury funding",
                    "timestamp": firestore.SERVER_TIMESTAMP,
                },
            )
            return True

        return commit(transaction)

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
            if role not in {"OWNER", "MANAGER", "AGENT"}:
                return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE
            if role == "AGENT" and recipient.get("agent_id") != sender_user_id:
                return WalletTransactionStatus.UNAUTHORIZED_DOWNLINE

            balance = sender.get("chip_balance", 0)
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
                return WalletTransactionStatus.INVALID_BALANCE
            if balance < amount:
                return WalletTransactionStatus.INSUFFICIENT_BALANCE

            transaction.update(sender_ref, {"chip_balance": firestore.Increment(-amount)})
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

            balance = player.get("chip_balance", 0)
            in_play = player.get("in_play_balance", 0)
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (balance, in_play)):
                return WalletTransactionStatus.INVALID_BALANCE
            if in_play > 0:
                return WalletTransactionStatus.PLAYER_HAS_CHIPS_IN_PLAY
            if balance < amount:
                return WalletTransactionStatus.INSUFFICIENT_BALANCE

            transaction.update(player_ref, {"chip_balance": firestore.Increment(-amount)})
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
        club_transaction_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("transactions")
            .document(buy_in_id)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> BuyInStatus:
            member_snapshot = member_ref.get(transaction=transaction)
            seat_snapshot = seat_ref.get(transaction=transaction)
            union_club_snapshot = union_club_ref.get(transaction=transaction)
            buy_in_snapshot = buy_in_ref.get(transaction=transaction)
            club_transaction_snapshot = club_transaction_ref.get(transaction=transaction)

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
        club_transaction_ref = (
            self._client.collection("clubs")
            .document(club_id)
            .collection("transactions")
            .document(buy_in_id)
        )
        transaction = self._client.transaction()

        @firestore.transactional
        def commit(transaction: Any) -> BuyInStatus:
            member_snapshot = member_ref.get(transaction=transaction)
            seat_snapshot = seat_ref.get(transaction=transaction)
            buy_in_snapshot = buy_in_ref.get(transaction=transaction)
            club_transaction_snapshot = club_transaction_ref.get(transaction=transaction)

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
            return BuyInStatus.RESERVED

        return commit(transaction)


def create_hand_repository(enabled: bool) -> NullHandRepository | FirestoreHandRepository:
    if not enabled:
        return NullHandRepository()
    return FirestoreHandRepository(firestore.Client())