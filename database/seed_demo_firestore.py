from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from google.cloud import firestore


DEMO_SEED_PATH = Path(__file__).parent / "fixtures" / "firestore_demo_seed.json"
ALLOWED_EMULATOR_HOSTS = {"localhost", "127.0.0.1", "::1"}


def load_manifest(path: Path = DEMO_SEED_PATH) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("mode") != "emulator-demo-only":
        raise ValueError("Seed manifest must declare emulator-demo-only mode.")
    if not isinstance(manifest.get("project_id"), str) or not manifest["project_id"].startswith("demo-"):
        raise ValueError("Seed manifest must use a Firebase demo project ID.")

    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Seed manifest must contain documents.")

    paths: set[str] = set()
    for document in documents:
        path = document.get("path")
        segments = path.split("/") if isinstance(path, str) else []
        if (
            not segments
            or len(segments) % 2 != 0
            or any(not segment or segment in {".", ".."} for segment in segments)
        ):
            raise ValueError(f"Invalid Firestore document path: {path!r}.")
        if path in paths:
            raise ValueError(f"Duplicate Firestore document path: {path!r}.")
        paths.add(path)
        data = document.get("data")
        if not isinstance(data, dict) or data.get("demo_only") is not True:
            raise ValueError(f"Document {path!r} must be marked demo_only.")

    relationships = manifest.get("relationships", [])
    for relationship in relationships:
        if relationship.get("from") not in paths or relationship.get("to") not in paths:
            raise ValueError(f"Relationship references an unseeded document: {relationship!r}.")


def require_local_emulator(manifest: dict[str, Any]) -> tuple[str, str]:
    emulator_host = os.getenv("FIRESTORE_EMULATOR_HOST", "")
    hostname = urlsplit(f"//{emulator_host}").hostname
    if hostname not in ALLOWED_EMULATOR_HOSTS:
        raise RuntimeError("Demo seeding requires FIRESTORE_EMULATOR_HOST on localhost.")

    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCLOUD_PROJECT")
        or manifest["project_id"]
    )
    if project_id != manifest["project_id"] or not project_id.startswith("demo-"):
        raise RuntimeError("Demo seeding only permits the manifest's Firebase demo project.")
    return emulator_host, project_id


def seed_demo_firestore() -> int:
    manifest = load_manifest()
    _, project_id = require_local_emulator(manifest)
    client = firestore.Client(project=project_id)
    batch = client.batch()
    for item in manifest["documents"]:
        batch.set(client.document(item["path"]), item["data"])
    batch.commit()
    return len(manifest["documents"])


def main() -> None:
    count = seed_demo_firestore()
    print(f"Seeded {count} demo documents into the local Firestore emulator.")


if __name__ == "__main__":
    main()