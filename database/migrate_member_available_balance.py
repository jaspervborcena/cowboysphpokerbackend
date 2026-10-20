from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any

from google.cloud import firestore


def field_migration_update(
    data: Mapping[str, Any],
    old_field: str,
    new_field: str,
) -> dict[str, Any] | None:
    if old_field not in data:
        return None
    old_value = data[old_field]
    new_value = data.get(new_field)
    if (
        isinstance(old_value, bool)
        or not isinstance(old_value, int)
    ):
        raise ValueError(f"{old_field} must contain an integer.")
    if new_field in data and (
        isinstance(new_value, bool)
        or not isinstance(new_value, int)
    ):
        raise ValueError(f"{new_field} must contain an integer.")
    if new_field in data and old_value != new_value:
        raise ValueError(
            f"Conflicting {old_field} and {new_field} values require manual review."
        )
    return {
        new_field: old_value if new_field not in data else new_value,
        old_field: firestore.DELETE_FIELD,
    }


def migrate_document_field(
    client: Any,
    reference: Any,
    old_field: str,
    new_field: str,
    *,
    apply: bool,
) -> bool:
    transaction = client.transaction()

    @firestore.transactional
    def commit(transaction: Any) -> bool:
        snapshot = reference.get(transaction=transaction)
        if not snapshot.exists:
            return False
        update = field_migration_update(
            snapshot.to_dict() or {}, old_field, new_field
        )
        if update is None:
            return False
        if apply:
            transaction.update(reference, update)
        return True

    return commit(transaction)


def migrate_member_balances(client: Any, *, apply: bool) -> tuple[int, int]:
    member_count = 0
    transaction_count = 0
    for club_snapshot in client.collection("clubs").stream():
        club_ref = client.collection("clubs").document(club_snapshot.id)
        for member_snapshot in club_ref.collection("members").stream():
            member_ref = club_ref.collection("members").document(member_snapshot.id)
            if migrate_document_field(
                client,
                member_ref,
                "chip_balance",
                "available_balance",
                apply=apply,
            ):
                member_count += 1
            for transaction_snapshot in member_ref.collection("transactions").stream():
                transaction_ref = member_ref.collection("transactions").document(
                    transaction_snapshot.id
                )
                if migrate_document_field(
                    client,
                    transaction_ref,
                    "chip_balance_delta",
                    "available_balance_delta",
                    apply=apply,
                ):
                    transaction_count += 1
    return member_count, transaction_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate club member balances to available_balance."
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Firestore project ID to scan (uses Application Default Credentials).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply writes. Without this flag, only report documents needing migration.",
    )
    args = parser.parse_args()
    client = firestore.Client(project=args.project)
    members, transactions = migrate_member_balances(client, apply=args.apply)
    mode = "Migrated" if args.apply else "Would migrate"
    print(
        f"{mode} {members} member balance document(s) and "
        f"{transactions} buy-in transaction document(s) in project {args.project}."
    )


if __name__ == "__main__":
    main()
