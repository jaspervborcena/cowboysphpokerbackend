from __future__ import annotations

import os

import pytest
from google.cloud import firestore

from database.seed_demo_firestore import (
    load_manifest,
    require_local_emulator,
    seed_demo_firestore,
    validate_manifest,
)


def test_demo_manifest_documents_and_relationships_are_connected() -> None:
    manifest = load_manifest()
    document_paths = {item["path"] for item in manifest["documents"]}

    assert manifest["mode"] == "emulator-demo-only"
    assert all(item["data"]["demo_only"] is True for item in manifest["documents"])
    assert all(
        relationship["from"] in document_paths
        and relationship["to"] in document_paths
        for relationship in manifest["relationships"]
    )


def test_manifest_rejects_relationship_to_unseeded_document() -> None:
    manifest = load_manifest()
    manifest["relationships"].append(
        {"from": manifest["documents"][0]["path"], "to": "missing/doc", "type": "invalid"}
    )

    with pytest.raises(ValueError, match="unseeded document"):
        validate_manifest(manifest)


def test_demo_seed_requires_local_firestore_emulator(monkeypatch) -> None:
    manifest = load_manifest()
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)

    with pytest.raises(RuntimeError, match="requires FIRESTORE_EMULATOR_HOST"):
        require_local_emulator(manifest)


def test_demo_seed_rejects_non_demo_project(monkeypatch) -> None:
    manifest = load_manifest()
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "production-project")

    with pytest.raises(RuntimeError, match="demo project"):
        require_local_emulator(manifest)


def test_demo_seed_accepts_only_local_emulator_and_manifest_project(monkeypatch) -> None:
    manifest = load_manifest()
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", manifest["project_id"])

    _, project_id = require_local_emulator(manifest)

    assert project_id == "demo-cowboys-ph-poker"


@pytest.mark.skipif(
    not os.getenv("FIRESTORE_EMULATOR_HOST"),
    reason="Run with firebase emulators:exec --only firestore.",
)
def test_demo_seed_inserts_connected_documents_in_firestore_emulator() -> None:
    manifest = load_manifest()
    assert seed_demo_firestore() == len(manifest["documents"])
    client = firestore.Client(project=manifest["project_id"])

    stored_documents = {
        item["path"]: client.document(item["path"]).get().to_dict()
        for item in manifest["documents"]
    }
    assert all(data and data.get("demo_only") is True for data in stored_documents.values())
    assert stored_documents["platform_wallets/global-playmoney-reserve"]["chip_balance"] == 999_980_000
    assert stored_documents[
        "platform_wallets/global-playmoney-reserve/transactions/demo-player-a-grant"
    ]["to_wallet_id"] == stored_documents["global_wallets/demo-player-a"]["wallet_id"]
    assert stored_documents["clubs/demo-club"]["owner_user_id"] == stored_documents[
        "clubs/demo-club/members/demo-owner"
    ]["user_id"]
    assert stored_documents["unions/demo-union/clubs/demo-club"]["club_id"] == stored_documents[
        "clubs/demo-club"
    ]["club_id"]
    assert stored_documents[
        "unions/demo-union/settlements/2026-10-17/hands/demo-union-hand"
    ]["hand_id"] == stored_documents[
        "unions/demo-union/tables/demo-union-table/hands/demo-union-hand"
    ]["hand_id"]