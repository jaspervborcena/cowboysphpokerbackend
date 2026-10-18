from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import (
    AuthenticatedUser,
    ClubMembership,
    ClubRole,
    get_active_club_member,
)
from app.wallet_api import app


def test_wallet_api_has_no_live_game_routes() -> None:
    spec = app.openapi()
    paths = set(spec["paths"])

    assert "/health" in paths
    assert "/me/wallets/global-playmoney/ensure" in paths
    assert "/me/wallets/global-playmoney" in paths
    assert "/clubs/{club_id}/members/me" in paths
    assert "/clubs/{club_id}/members/me/wallet" in paths
    assert "/clubs/{club_id}/rake/players" in paths
    assert "/clubs/{club_id}/tables/{table_id}" not in paths
    assert not any(path.startswith("/ws/") for path in paths)

    ensure_operation = spec["paths"]["/me/wallets/global-playmoney/ensure"]["post"]
    assert ensure_operation["tags"] == ["Global wallet"]
    assert ensure_operation["security"]
    assert "platform reserve" in ensure_operation["description"].lower()
    assert "does not debit" not in ensure_operation["description"].lower()
    assert "GlobalWalletEnsureResponse" in spec["components"]["schemas"]
    assert "401" in ensure_operation["responses"]

    create_club_operation = spec["paths"]["/platform/clubs"]["post"]
    assert "platform reserve" in create_club_operation["description"].lower()


def test_wallet_api_health_and_auth_error_contract() -> None:
    with TestClient(app) as client:
        health = client.get("/health")
        wallet = client.get("/me/wallets/global-playmoney")
        rake_stats = client.get("/clubs/club-1/rake/players")

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "service": "wallet-api"}
    assert wallet.status_code == 401
    assert wallet.json()["code"] == 40101
    assert rake_stats.status_code == 401


def test_player_cannot_read_club_player_rake_stats() -> None:
    app.dependency_overrides[get_active_club_member] = lambda: ClubMembership(
        AuthenticatedUser(uid="player-1"),
        ClubRole.PLAYER,
        {"status": "active", "role": "PLAYER"},
    )
    try:
        with TestClient(app) as client:
            response = client.get("/clubs/club-1/rake/players")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("role", "expected_agent_user_id"),
    [
        (ClubRole.OWNER, None),
        (ClubRole.MANAGER, None),
        (ClubRole.AGENT, "agent-1"),
    ],
)
def test_authorized_roles_can_read_club_player_rake_stats(
    monkeypatch, role: ClubRole, expected_agent_user_id: str | None
) -> None:
    app.dependency_overrides[get_active_club_member] = lambda: ClubMembership(
        AuthenticatedUser(uid="agent-1" if role is ClubRole.AGENT else "operator-1"),
        role,
        {"status": "active", "role": role.value},
    )
    calls: list[tuple[str, str | None]] = []

    def list_stats(club_id: str, agent_user_id: str | None = None):
        calls.append((club_id, agent_user_id))
        return [
            {
                "user_id": "player-1",
                "username": "Player One",
                "generated_rake": 40,
                "hands_played": 10,
            }
        ]

    monkeypatch.setattr(app.state.repository, "list_club_player_rake_stats", list_stats)
    try:
        with TestClient(app) as client:
            response = client.get("/clubs/club-1/rake/players")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "user_id": "player-1",
            "username": "Player One",
            "generated_rake": 40,
            "hands_played": 10,
        }
    ]
    assert calls == [("club-1", expected_agent_user_id)]