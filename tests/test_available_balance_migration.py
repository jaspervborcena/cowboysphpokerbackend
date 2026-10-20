import pytest
from google.cloud import firestore

from database.migrate_member_available_balance import field_migration_update


def test_balance_migration_renames_legacy_member_field() -> None:
    update = field_migration_update(
        {"chip_balance": 1200},
        "chip_balance",
        "available_balance",
    )

    assert update == {
        "available_balance": 1200,
        "chip_balance": firestore.DELETE_FIELD,
    }


def test_balance_migration_removes_matching_legacy_duplicate() -> None:
    update = field_migration_update(
        {"chip_balance": 1200, "available_balance": 1200},
        "chip_balance",
        "available_balance",
    )

    assert update == {
        "available_balance": 1200,
        "chip_balance": firestore.DELETE_FIELD,
    }


def test_balance_migration_rejects_conflicting_values() -> None:
    with pytest.raises(ValueError, match="Conflicting"):
        field_migration_update(
            {"chip_balance": 1200, "available_balance": 1000},
            "chip_balance",
            "available_balance",
        )


def test_balance_migration_rejects_non_integer_values() -> None:
    with pytest.raises(ValueError, match="must contain an integer"):
        field_migration_update(
            {"chip_balance": True},
            "chip_balance",
            "available_balance",
        )


def test_balance_migration_leaves_documents_without_legacy_field_unchanged() -> None:
    assert (
        field_migration_update(
            {"available_balance": 1200},
            "chip_balance",
            "available_balance",
        )
        is None
    )
