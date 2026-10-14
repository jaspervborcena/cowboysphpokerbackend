from fastapi.testclient import TestClient

from app import main as api_main
from app.main import app, tables
from database import BuyInStatus, NullHandRepository
from logic.auth import InvalidFirebaseToken


class FakeTokenVerifier:
    def verify_id_token(self, token: str) -> dict[str, str]:
        if not token.startswith("valid:"):
            raise InvalidFirebaseToken("Invalid token.")
        uid = token.removeprefix("valid:")
        return {"uid": uid, "name": f"Display {uid}"}


class FakeHandRepository:
    def __init__(self, balances: dict[str, int], roles: dict[str, str] | None = None) -> None:
        self.balances = balances
        self.roles = roles or {}
        self.seats: dict[tuple[str, str, int], dict[str, object]] = {}
        self.buy_ins: dict[str, dict[str, object]] = {}
        self.hands: dict[tuple[str, str], dict[str, object]] = {}

    def get_club_member(self, club_id: str, user_id: str) -> dict[str, str] | None:
        if user_id not in self.balances:
            return None
        return {
            "status": "active",
            "role": self.roles.get(user_id, "owner"),
            "username": f"Name {user_id}",
        }

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
        seat_key = (club_id, table_id, seat_index)
        prior_buy_in = self.buy_ins.get(buy_in_id)
        if prior_buy_in is not None:
            if prior_buy_in["seat_key"] == seat_key and prior_buy_in["user_id"] == user_id and prior_buy_in["amount"] == buy_in_amount:
                return BuyInStatus.ALREADY_RESERVED
            return BuyInStatus.IDEMPOTENCY_CONFLICT
        if seat_key in self.seats:
            return BuyInStatus.SEAT_OCCUPIED
        if user_id not in self.balances:
            return BuyInStatus.MEMBER_NOT_FOUND
        if self.balances[user_id] < buy_in_amount:
            return BuyInStatus.INSUFFICIENT_BALANCE

        self.balances[user_id] -= buy_in_amount
        reservation = {
            "seat_key": seat_key,
            "user_id": user_id,
            "amount": buy_in_amount,
            "buy_in_id": buy_in_id,
        }
        self.seats[seat_key] = reservation
        self.buy_ins[buy_in_id] = reservation
        return BuyInStatus.RESERVED

    def get_hand_history(
        self, club_id: str, hand_id: str
    ) -> dict[str, object] | None:
        return self.hands.get((club_id, hand_id))

    def persist_hand_history_and_ledgers(
        self,
        club_id: str,
        table_id: str,
        hand_summary: dict[str, object],
        chip_deltas: dict[str, int],
    ) -> bool:
        return True


def configure_test_dependencies(monkeypatch, repository: FakeHandRepository | NullHandRepository) -> None:
    monkeypatch.setattr(api_main, "repository", repository)
    monkeypatch.setattr(api_main.app.state, "repository", repository)
    monkeypatch.setattr(api_main.app.state, "token_verifier", FakeTokenVerifier())


def test_table_api_and_player_websockets(monkeypatch) -> None:
    club_id = "api-test-club"
    table_id = "api-test-table"
    table_key = (club_id, table_id)
    repository = FakeHandRepository(
        {"club-owner": 1000, "api-player-a": 1000, "api-player-b": 1000},
        {"club-owner": "OWNER", "api-player-a": "PLAYER", "api-player-b": "PLAYER"},
    )
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "small_blind": 10,
                "big_blind": 20,
                "buy_in_min": 20,
                "buy_in_max": 1000,
            },
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert response.status_code == 201

        for seat_index, user_id in enumerate(("api-player-a", "api-player-b")):
            response = client.post(
                f"/clubs/{club_id}/tables/{table_id}/seats",
                json={
                    "seat_index": seat_index,
                    "buy_in_amount": 500,
                    "buy_in_id": f"00000000-0000-0000-0000-{seat_index + 1:012d}",
                },
                headers={"Authorization": f"Bearer valid:{user_id}"},
            )
            assert response.status_code == 201
        assert repository.balances == {
            "club-owner": 1000,
            "api-player-a": 500,
            "api-player-b": 500,
        }

        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}/hands",
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert response.status_code == 201
        actor_user_id = tables[table_key].game.current_actor_user_id
        assert actor_user_id is not None
        other_user_id = "api-player-b" if actor_user_id == "api-player-a" else "api-player-a"

        with client.websocket_connect(
            f"/ws/table/{club_id}/{table_id}"
        ) as actor_socket:
            actor_socket.send_json({"type": "authenticate", "id_token": f"valid:{actor_user_id}"})
            assert actor_socket.receive_json() == {
                "type": "authenticated",
                "user_id": actor_user_id,
            }
            actor_state = actor_socket.receive_json()
            actor = next(
                player for player in actor_state["players"]
                if player["user_id"] == actor_user_id
            )
            assert len(actor["hole_cards"]) == 2

            with client.websocket_connect(
                f"/ws/table/{club_id}/{table_id}"
            ) as other_socket:
                other_socket.send_json({"type": "authenticate", "id_token": f"valid:{other_user_id}"})
                assert other_socket.receive_json() == {
                    "type": "authenticated",
                    "user_id": other_user_id,
                }
                other_state = other_socket.receive_json()
                hidden_actor = next(
                    player for player in other_state["players"]
                    if player["user_id"] == actor_user_id
                )
                assert hidden_actor["hole_cards"] == ["??", "??"]

                actor_socket.send_json({"action": "FOLD"})
                assert actor_socket.receive_json()["is_hand_running"] is False
                assert other_socket.receive_json()["is_hand_running"] is False

    tables.pop(table_key, None)


def test_current_membership_returns_authoritative_role(monkeypatch) -> None:
    repository = FakeHandRepository(
        {"club-agent": 0},
        {"club-agent": "AGENT"},
    )
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.get(
            "/clubs/demo/members/me",
            headers={"Authorization": "Bearer valid:club-agent"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "club_id": "demo",
        "user_id": "club-agent",
        "username": "Name club-agent",
        "role": "AGENT",
    }


def test_hand_replay_requires_membership_and_returns_filtered_frames(monkeypatch) -> None:
    club_id = "replay-test-club"
    hand_id = "replay-hand"
    repository = FakeHandRepository(
        {"replay-player": 0},
        {"replay-player": "PLAYER"},
    )
    repository.hands[(club_id, hand_id)] = {
        "hand_id": hand_id,
        "club_id": club_id,
        "timestamp": "2026-10-14T10:00:00Z",
        "final_board": ["As", "3h", "5d"],
        "pot_total": 450,
        "is_showdown": False,
        "winners": [],
        "deck_state": {"remaining_cards": ["private-card"]},
        "frames": [
            {
                "frame_index": 0,
                "street": 0,
                "community_cards": [],
                "pot": 150,
                "active_seat": 1,
                "last_action": None,
                "players": [
                    {
                        "user_id": "replay-player",
                        "seat": 0,
                        "stack": 450,
                        "hole_cards": ["As", "Ad"],
                        "mucked": False,
                        "in_hand": True,
                    },
                    {
                        "user_id": "other-player",
                        "seat": 1,
                        "stack": 400,
                        "hole_cards": ["Kd", "Qd"],
                        "mucked": False,
                        "in_hand": False,
                    },
                ],
            }
        ],
        "operations": [{"type": "PrivateAuditOnly"}],
    }
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        unauthenticated = client.get(f"/clubs/{club_id}/hands/{hand_id}/replay")
        non_member = client.get(
            f"/clubs/{club_id}/hands/{hand_id}/replay",
            headers={"Authorization": "Bearer valid:unknown-user"},
        )
        response = client.get(
            f"/clubs/{club_id}/hands/{hand_id}/replay",
            headers={"Authorization": "Bearer valid:replay-player"},
        )

    assert unauthenticated.status_code == 403
    assert non_member.status_code == 403
    assert response.status_code == 200
    replay = response.json()
    assert replay["frames"][0]["players"][0]["hole_cards"] == ["As", "Ad"]
    assert replay["frames"][0]["players"][1]["hole_cards"] == ["??", "??"]
    assert "deck_state" not in replay
    assert "operations" not in replay


def test_seating_rejects_insufficient_balance_and_client_stack(monkeypatch) -> None:
    club_id = "buy-in-test-club"
    table_id = "buy-in-test-table"
    table_key = (club_id, table_id)
    repository = FakeHandRepository(
        {"club-owner": 1000, "player-low-balance": 25},
        {"club-owner": "OWNER", "player-low-balance": "PLAYER"},
    )
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "small_blind": 10,
                "big_blind": 20,
                "buy_in_min": 20,
                "buy_in_max": 1000,
            },
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert response.status_code == 201

        old_request = client.post(
            f"/clubs/{club_id}/tables/{table_id}/seats",
            json={
                "seat_index": 0,
                "chip_stack": 500,
            },
            headers={"Authorization": "Bearer valid:player-low-balance"},
        )
        assert old_request.status_code == 422

        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}/seats",
            json={
                "seat_index": 0,
                "buy_in_amount": 100,
                "buy_in_id": "00000000-0000-0000-0000-000000000001",
            },
            headers={"Authorization": "Bearer valid:player-low-balance"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Insufficient club chip balance."
        assert tables[table_key].game.seats == {}
        assert repository.balances["player-low-balance"] == 25

    tables.pop(table_key, None)


def test_seating_fails_closed_when_firestore_is_disabled(monkeypatch) -> None:
    club_id = "firestore-disabled-club"
    table_id = "firestore-disabled-table"
    table_key = (club_id, table_id)
    configure_test_dependencies(monkeypatch, NullHandRepository())

    with TestClient(app) as client:
        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "small_blind": 10,
                "big_blind": 20,
                "buy_in_min": 20,
                "buy_in_max": 1000,
            },
            headers={"Authorization": "Bearer valid:player-a"},
        )
        assert response.status_code == 503
        assert table_key not in tables


def test_rest_rejects_missing_and_invalid_firebase_tokens(monkeypatch) -> None:
    repository = FakeHandRepository({"club-owner": 1000}, {"club-owner": "OWNER"})
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        missing = client.post(
            "/clubs/auth-test/tables/table-1",
            json={"small_blind": 10, "big_blind": 20, "buy_in_min": 20, "buy_in_max": 1000},
        )
        invalid = client.post(
            "/clubs/auth-test/tables/table-1",
            json={"small_blind": 10, "big_blind": 20, "buy_in_min": 20, "buy_in_max": 1000},
            headers={"Authorization": "Bearer expired-token"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_non_admin_member_cannot_create_table(monkeypatch) -> None:
    repository = FakeHandRepository({"regular-member": 1000}, {"regular-member": "PLAYER"})
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            "/clubs/role-test/tables/table-1",
            json={"small_blind": 10, "big_blind": 20, "buy_in_min": 20, "buy_in_max": 1000},
            headers={"Authorization": "Bearer valid:regular-member"},
        )

    assert response.status_code == 403