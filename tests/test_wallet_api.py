from __future__ import annotations

from fastapi.testclient import TestClient

from app.wallet_api import app


def test_wallet_api_has_no_live_game_routes() -> None:
    spec = app.openapi()
    paths = set(spec["paths"])

    assert "/health" in paths
    assert "/me/wallets/global-playmoney/ensure" in paths
    assert "/me/wallets/global-playmoney" in paths
    assert "/clubs/{club_id}/members/me" in paths
    assert "/clubs/{club_id}/members/me/wallet" in paths
    assert "/clubs/{club_id}/tables/{table_id}" not in paths
    assert not any(path.startswith("/ws/") for path in paths)

    ensure_operation = spec["paths"]["/me/wallets/global-playmoney/ensure"]["post"]
    assert ensure_operation["tags"] == ["Global wallet"]
    assert ensure_operation["security"]
    assert "GlobalWalletEnsureResponse" in spec["components"]["schemas"]
    assert "401" in ensure_operation["responses"]


def test_wallet_api_health_and_auth_error_contract() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        wallet = client.get("/me/wallets/global-playmoney")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "wallet-api"}
    assert wallet.status_code == 401
    assert wallet.json()["code"] == 40101