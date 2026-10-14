import asyncio

from fastapi.testclient import TestClient

from app import main as api_main
from app.main import app, tables
from database import BuyInStatus, NullHandRepository, WalletTransactionStatus
from base_poker_game import BasePokerGame
from logic.connections import ConnectionManager
from logic.runtime import TableRuntime
from logic.auth import InvalidFirebaseToken


class FakeTokenVerifier:
    def verify_id_token(self, token: str) -> dict[str, str | bool]:
        if not token.startswith("valid:"):
            raise InvalidFirebaseToken("Invalid token.")
        uid = token.removeprefix("valid:")
        return {
            "uid": uid,
            "name": f"Display {uid}",
            "platform_admin": uid == "platform-admin",
        }


class FakeHandRepository:
    def __init__(self, balances: dict[str, int], roles: dict[str, str] | None = None) -> None:
        self.balances = balances
        self.roles = roles or {}
        self.seats: dict[tuple[str, str, int], dict[str, object]] = {}
        self.buy_ins: dict[str, dict[str, object]] = {}
        self.hands: dict[tuple[str, str], dict[str, object]] = {}
        self.union_hands: dict[tuple[str, str], dict[str, object]] = {}
        self.union_admins: dict[tuple[str, str], dict[str, str]] = {}
        self.union_clubs: set[tuple[str, str]] = set()
        self.union_table_records: dict[tuple[str, str], dict[str, object]] = {}
        self.union_hand_summaries: list[dict[str, object]] = []
        self.wallet_calls: list[tuple[object, ...]] = []
        self.interclub_calls: list[tuple[object, ...]] = []
        self.club_wallet_balances: dict[str, int] = {}
        self.created_clubs: list[tuple[str, str, str, str]] = []

    def recover_pending_settlements(self) -> int:
        return 0

    def create_club(
        self,
        club_id: str,
        name: str,
        owner_user_id: str,
        created_by: str,
    ) -> bool:
        self.created_clubs.append((club_id, name, owner_user_id, created_by))
        return True

    def get_club_wallet_balance(self, club_id: str) -> int:
        return self.club_wallet_balances.get(club_id, 0)

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
        self.interclub_calls.append(
            (source_club_id, destination_club_id, operator_user_id, amount, transaction_id, performed_by, note)
        )
        return WalletTransactionStatus.COMPLETED

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
        self.wallet_calls.append(("SEND_OUT", club_id, sender_user_id, recipient_user_id, amount, transaction_id, performed_by, note))
        return WalletTransactionStatus.COMPLETED

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
        self.wallet_calls.append(("CLAIM_BACK", club_id, player_user_id, recipient_user_id, amount, transaction_id, performed_by, note))
        return WalletTransactionStatus.COMPLETED

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

    def get_union_hand_history(
        self, union_id: str, hand_id: str
    ) -> dict[str, object] | None:
        return self.union_hands.get((union_id, hand_id))

    def get_union_admin(self, union_id: str, user_id: str) -> dict[str, str] | None:
        return self.union_admins.get((union_id, user_id))

    def is_club_in_union(self, union_id: str, club_id: str) -> bool:
        return (union_id, club_id) in self.union_clubs

    def get_unions_for_club(self, club_id: str) -> list[dict[str, str]]:
        return [
            {"union_id": union_id, "name": f"Union {union_id}"}
            for union_id, member_club_id in sorted(self.union_clubs)
            if member_club_id == club_id
        ]

    def set_union_admin(
        self,
        union_id: str,
        user_id: str,
        role: str,
        status: str,
    ) -> bool:
        self.union_admins[(union_id, user_id)] = {"role": role, "status": status}
        return True

    def add_union_club(
        self,
        union_id: str,
        club_id: str,
        club_data: dict[str, object],
    ) -> bool:
        self.union_clubs.add((union_id, club_id))
        return True

    def create_union_table_record(
        self, union_id: str, table_id: str, table_data: dict[str, object]
    ) -> bool:
        key = (union_id, table_id)
        if key in self.union_table_records:
            return False
        self.union_table_records[key] = table_data
        return True

    def close_union_table_record(self, union_id: str, table_id: str) -> bool:
        record = self.union_table_records.get((union_id, table_id))
        if record is None:
            return False
        record["status"] = "closed"
        return True

    def has_union_table_seats(self, union_id: str, table_id: str) -> bool:
        scope = f"union:{union_id}"
        return any(
            seat_key[0] == scope and seat_key[1] == table_id
            for seat_key in self.seats
        )

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
        seat_key = (f"union:{union_id}", table_id, seat_index)
        if buy_in_id in self.buy_ins:
            prior = self.buy_ins[buy_in_id]
            if prior.get("seat_key") == seat_key and prior.get("user_id") == user_id:
                return BuyInStatus.ALREADY_RESERVED
            return BuyInStatus.IDEMPOTENCY_CONFLICT
        if (union_id, club_id) not in self.union_clubs:
            return BuyInStatus.UNION_CLUB_NOT_FOUND
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
            "club_id": club_id,
            "amount": buy_in_amount,
            "buy_in_id": buy_in_id,
        }
        self.seats[seat_key] = reservation
        self.buy_ins[buy_in_id] = reservation
        return BuyInStatus.RESERVED

    def persist_hand_history_and_ledgers(
        self,
        club_id: str,
        table_id: str,
        hand_summary: dict[str, object],
        chip_deltas: dict[str, int],
    ) -> bool:
        return True

    def persist_union_hand_history_and_ledgers(
        self,
        union_id: str,
        table_id: str,
        hand_summary: dict[str, object],
        chip_deltas: dict[str, int],
        player_clubs: dict[str, str],
    ) -> bool:
        self.union_hand_summaries.append(hand_summary)
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

                other_socket.send_json(
                    {"type": "pre_action", "action": "CHECK_FOLD"}
                )
                assert other_socket.receive_json() == {
                    "type": "pre_action_set",
                    "action": "CHECK_FOLD",
                }

                runtime = tables[table_key]
                actor_action = "CALL" if (runtime.game.current_call_amount or 0) > 0 else "CHECK"
                actor_socket.send_json({"action": actor_action})
                actor_socket.receive_json()
                other_snapshot = other_socket.receive_json()
                assert other_snapshot["pre_action"] is None
                assert runtime.game._replay_frames[-1]["last_action"]["user_id"] == other_user_id
                assert runtime.game._replay_frames[-1]["last_action"]["action"] in {"CHECK", "FOLD"}

    tables.pop(table_key, None)


def test_provision_union_admin_and_club_membership(monkeypatch) -> None:
    repository = FakeHandRepository(
        {"union-admin": 1000, "club-owner": 1000},
        {"union-admin": "PLAYER", "club-owner": "OWNER"},
    )
    repository.union_admins[("union-42", "union-admin")] = {
        "role": "UNION_ADMIN",
        "status": "active",
    }
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            "/unions/union-42/admins/new-admin",
            json={"role": "UNION_ADMIN", "status": "active"},
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert response.status_code == 201
        assert repository.union_admins[("union-42", "new-admin")] == {
            "role": "UNION_ADMIN",
            "status": "active",
        }

        response = client.post(
            "/unions/union-42/clubs/club-b",
            json={
                "club_id": "club-b",
                "union_name": "Metro Poker League",
                "status": "active",
            },
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert response.status_code == 201
        assert ("union-42", "club-b") in repository.union_clubs


def test_table_settings_support_jackpot_configuration(monkeypatch) -> None:
    repository = FakeHandRepository(
        {"table-admin": 1000},
        {"table-admin": "MANAGER"},
    )
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            "/clubs/demo/tables/table-settings",
            json={
                "small_blind": 10,
                "big_blind": 20,
                "buy_in_min": 20,
                "buy_in_max": 500,
            },
            headers={"Authorization": "Bearer valid:table-admin"},
        )
        assert response.status_code == 201

        response = client.patch(
            "/clubs/demo/tables/table-settings/settings",
            json={
                "jackpot_enabled": True,
                "jackpot_fee": 25,
                "jackpot_min_pot": 200,
                "jackpot_qualifying_hand": "quads-or-better",
                "rake_rate": 0.05,
                "rake_cap": 50,
                "rake_min_pot": 100,
                "hand_of_day_enabled": True,
                "hand_of_day_target": 500,
                "hand_of_day_prize": 100,
            },
            headers={"Authorization": "Bearer valid:table-admin"},
        )
        assert response.status_code == 200
        assert response.json()["jackpot_enabled"] is True
        assert response.json()["jackpot_fee"] == 25
        assert response.json()["jackpot_min_pot"] == 200
        assert response.json()["rake_rate"] == 0.05
        assert response.json()["rake_cap"] == 50
        assert response.json()["rake_min_pot"] == 100
        assert response.json()["hand_of_day_enabled"] is True
        assert response.json()["hand_of_day_target"] == 500
        assert response.json()["hand_of_day_prize"] == 100

        response = client.get(
            "/clubs/demo/tables/table-settings",
            headers={"Authorization": "Bearer valid:table-admin"},
        )
        assert response.status_code == 200
        entry = response.json()
        assert entry["jackpot_enabled"] is True
        assert entry["jackpot_fee"] == 25
        assert entry["jackpot_min_pot"] == 200
        assert entry["rake_rate"] == 0.05
        assert entry["rake_cap"] == 50
        assert entry["rake_min_pot"] == 100
        assert entry["hand_of_day_enabled"] is True
        assert entry["hand_of_day_target"] == 500
        assert entry["hand_of_day_prize"] == 100


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


def test_union_hand_replay_requires_affiliated_club_and_masks_cards(monkeypatch) -> None:
    union_id = "replay-union"
    hand_id = "union-hand"
    repository = FakeHandRepository(
        {"union-replay-player": 0},
        {"union-replay-player": "PLAYER"},
    )
    repository.union_clubs.add((union_id, "club-a"))
    repository.union_hands[(union_id, hand_id)] = {
        "hand_id": hand_id,
        "union_id": union_id,
        "final_board": [],
        "is_showdown": False,
        "frames": [{
            "frame_index": 0,
            "players": [
                {"user_id": "union-replay-player", "club_id": "club-a", "hole_cards": ["As", "Ad"]},
                {"user_id": "other-player", "club_id": "club-b", "hole_cards": ["Ks", "Kd"]},
            ],
        }],
        "operations": [{"type": "PrivateAuditOnly"}],
        "deck_state": {"remaining_cards": ["secret-card"]},
    }
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        non_member = client.get(
            f"/unions/{union_id}/hands/{hand_id}/replay?club_id=club-b",
            headers={"Authorization": "Bearer valid:union-replay-player"},
        )
        response = client.get(
            f"/unions/{union_id}/hands/{hand_id}/replay?club_id=club-a",
            headers={"Authorization": "Bearer valid:union-replay-player"},
        )

    assert non_member.status_code == 403
    assert response.status_code == 200
    replay = response.json()
    assert replay["frames"][0]["players"][0]["hole_cards"] == ["As", "Ad"]
    assert replay["frames"][0]["players"][1]["hole_cards"] == ["??", "??"]
    assert "operations" not in replay
    assert "deck_state" not in replay


def test_local_angular_origin_can_make_authorized_cors_requests() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/clubs/demo/tables",
            headers={
                "Origin": "http://localhost:4200",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"


def test_union_table_is_admin_created_and_shared_across_member_clubs(monkeypatch) -> None:
    union_id = "union-1"
    table_id = "shared-table"
    table_key = (f"union:{union_id}", table_id)
    repository = FakeHandRepository(
        {"club-owner": 1000, "union-player-a": 1000, "union-player-b": 1000},
        {
            "club-owner": "OWNER",
            "union-player-a": "PLAYER",
            "union-player-b": "PLAYER",
        },
    )
    repository.union_admins[(union_id, "union-admin")] = {
        "status": "active",
        "role": "UNION_ADMIN",
    }
    repository.union_clubs.update({(union_id, "club-a"), (union_id, "club-b")})
    configure_test_dependencies(monkeypatch, repository)

    table_config = {
        "origin_club_id": "club-a",
        "table_name": "Union Main",
        "small_blind": 10,
        "big_blind": 20,
        "buy_in_min": 20,
        "buy_in_max": 1000,
    }
    with TestClient(app) as client:
        owner_response = client.post(
            f"/unions/{union_id}/tables/{table_id}",
            json=table_config,
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert owner_response.status_code == 403

        created = client.post(
            f"/unions/{union_id}/tables/{table_id}",
            json=table_config,
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert created.status_code == 201
        empty_table = client.post(
            f"/unions/{union_id}/tables/empty-table",
            json=table_config,
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert empty_table.status_code == 201
        owner_delete = client.delete(
            f"/unions/{union_id}/tables/empty-table",
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        admin_delete = client.delete(
            f"/unions/{union_id}/tables/empty-table",
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert owner_delete.status_code == 403
        assert admin_delete.status_code == 204
        unions_response = client.get(
            "/clubs/club-a/unions",
            headers={"Authorization": "Bearer valid:union-player-a"},
        )
        assert unions_response.json() == [{"union_id": union_id, "name": f"Union {union_id}"}]
        tables_response = client.get(
            f"/clubs/club-b/unions/{union_id}/tables",
            headers={"Authorization": "Bearer valid:union-player-b"},
        )
        assert tables_response.status_code == 200
        assert tables_response.json()[0]["is_union_table"] is True
        assert tables_response.json()[0]["origin_club_id"] == "club-a"

        for seat_index, (club_id, user_id) in enumerate(
            (("club-a", "union-player-a"), ("club-b", "union-player-b"))
        ):
            response = client.post(
                f"/unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats",
                json={
                    "seat_index": seat_index,
                    "buy_in_amount": 500,
                    "buy_in_id": f"00000000-0000-0000-0000-{seat_index + 1:012d}",
                },
                headers={"Authorization": f"Bearer valid:{user_id}"},
            )
            assert response.status_code == 201
            assert response.json()["players"][seat_index]["club_id"] == club_id

        occupied_delete = client.delete(
            f"/unions/{union_id}/tables/{table_id}",
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert occupied_delete.status_code == 409

        start = client.post(
            f"/unions/{union_id}/tables/{table_id}/hands",
            headers={"Authorization": "Bearer valid:union-admin"},
        )
        assert start.status_code == 201

        with client.websocket_connect(f"/ws/union/{union_id}/{table_id}") as socket_a:
            socket_a.send_json(
                {
                    "type": "authenticate",
                    "id_token": "valid:union-player-a",
                    "club_id": "club-a",
                }
            )
            assert socket_a.receive_json()["type"] == "authenticated"
            state_a = socket_a.receive_json()
            assert {player["club_id"] for player in state_a["players"]} == {"club-a", "club-b"}

            with client.websocket_connect(f"/ws/union/{union_id}/{table_id}") as socket_b:
                socket_b.send_json(
                    {
                        "type": "authenticate",
                        "id_token": "valid:union-player-b",
                        "club_id": "club-b",
                    }
                )
                assert socket_b.receive_json()["type"] == "authenticated"
                state_b = socket_b.receive_json()
                actor_user_id = tables[table_key].game.current_actor_user_id
                selected_socket = socket_a if actor_user_id == "union-player-a" else socket_b
                other_socket = socket_b if selected_socket is socket_a else socket_a
                selected_socket.send_json({"action": "FOLD"})
                assert selected_socket.receive_json()["is_hand_running"] is False
                assert other_socket.receive_json()["is_hand_running"] is False

    assert len(repository.union_hand_summaries) == 1
    assert repository.balances["union-player-a"] == 500
    assert repository.balances["union-player-b"] == 500
    tables.pop(table_key, None)


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


def test_club_wallet_routes_require_operator_role_and_use_authenticated_identity(monkeypatch) -> None:
    repository = FakeHandRepository(
        {"club-owner": 1000, "regular-player": 500},
        {"club-owner": "OWNER", "regular-player": "PLAYER"},
    )
    configure_test_dependencies(monkeypatch, repository)
    transaction_id = "00000000-0000-0000-0000-000000000010"

    with TestClient(app) as client:
        sent = client.post(
            "/clubs/wallet-test/wallet/send-out",
            json={"user_id": "regular-player", "amount": 100, "transaction_id": transaction_id},
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        claimed = client.post(
            "/clubs/wallet-test/wallet/claim-back",
            json={"user_id": "regular-player", "amount": 50, "transaction_id": "00000000-0000-0000-0000-000000000011"},
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        denied = client.post(
            "/clubs/wallet-test/wallet/send-out",
            json={"user_id": "club-owner", "amount": 1, "transaction_id": "00000000-0000-0000-0000-000000000012"},
            headers={"Authorization": "Bearer valid:regular-player"},
        )

    assert sent.status_code == 200
    assert sent.json()["from_user_id"] == "club-owner"
    assert claimed.status_code == 200
    assert claimed.json()["to_user_id"] == "club-owner"
    assert denied.status_code == 403
    assert repository.wallet_calls == [
        ("SEND_OUT", "wallet-test", "club-owner", "regular-player", 100, transaction_id, "club-owner", ""),
        ("CLAIM_BACK", "wallet-test", "regular-player", "club-owner", 50, "00000000-0000-0000-0000-000000000011", "club-owner", ""),
    ]


def test_platform_admin_creates_club_with_initial_treasury_funds(monkeypatch) -> None:
    repository = FakeHandRepository({})
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        created = client.post(
            "/platform/clubs",
            json={"name": "New Club", "owner_user_id": "new-owner"},
            headers={"Authorization": "Bearer valid:platform-admin"},
        )
        denied = client.post(
            "/platform/clubs",
            json={"name": "Unauthorized Club", "owner_user_id": "regular-user"},
            headers={"Authorization": "Bearer valid:regular-user"},
        )

    assert created.status_code == 201
    assert created.json()["treasury_balance"] == 10000
    assert created.json()["owner_user_id"] == "new-owner"
    assert denied.status_code == 403
    assert len(repository.created_clubs) == 1
    assert repository.created_clubs[0][1:] == (
        "New Club",
        "new-owner",
        "platform-admin",
    )


def test_inter_club_wallet_routes_are_limited_to_owners_and_managers(monkeypatch) -> None:
    repository = FakeHandRepository(
        {"owner-1": 0, "manager-1": 0, "agent-1": 0, "player-1": 0},
        {
            "owner-1": "OWNER",
            "manager-1": "MANAGER",
            "agent-1": "AGENT",
            "player-1": "PLAYER",
        },
    )
    repository.club_wallet_balances["club-1"] = 7500
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        balance = client.get(
            "/clubs/club-1/wallet",
            headers={"Authorization": "Bearer valid:manager-1"},
        )
        sent = client.post(
            "/clubs/club-1/wallet/transfers",
            json={
                "destination_club_id": "club-2",
                "amount": 1200,
                "transaction_id": "00000000-0000-0000-0000-000000000020",
                "note": "Inter-club funding",
            },
            headers={"Authorization": "Bearer valid:owner-1"},
        )
        agent_denied = client.post(
            "/clubs/club-1/wallet/transfers",
            json={
                "destination_club_id": "club-2",
                "amount": 100,
                "transaction_id": "00000000-0000-0000-0000-000000000021",
            },
            headers={"Authorization": "Bearer valid:agent-1"},
        )
        player_denied = client.get(
            "/clubs/club-1/wallet",
            headers={"Authorization": "Bearer valid:player-1"},
        )

    assert balance.status_code == 200
    assert balance.json()["chip_balance"] == 7500
    assert sent.status_code == 200
    assert sent.json()["from_club_id"] == "club-1"
    assert sent.json()["to_club_id"] == "club-2"
    assert agent_denied.status_code == 403
    assert player_denied.status_code == 403
    assert repository.interclub_calls == [
        (
            "club-1",
            "club-2",
            "owner-1",
            1200,
            "00000000-0000-0000-0000-000000000020",
            "owner-1",
            "Inter-club funding",
        )
    ]


def test_runtime_retains_completed_hand_until_repository_acknowledges_it() -> None:
    class FailOnceRepository:
        def __init__(self) -> None:
            self.calls = 0

        def persist_hand_history_and_ledgers(self, *args, **kwargs) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary Firestore failure")
            return True

    async def run_test() -> None:
        game = BasePokerGame("retry-table", 10, 20, 20, 1000)
        assert game.add_player(0, "player-a", "Alice", 500)
        assert game.add_player(1, "player-b", "Bob", 500)
        repository = FailOnceRepository()
        runtime = TableRuntime(
            "club-1", game, ConnectionManager(), repository, turn_timeout_seconds=0
        )
        assert await runtime.start_hand()
        actor = game.current_actor_user_id
        assert actor is not None

        try:
            await runtime.act(actor, "FOLD")
        except RuntimeError:
            pass
        else:
            raise AssertionError("The simulated persistence failure should propagate.")

        pending = game.peek_completed_hand()
        assert pending is not None
        assert await runtime.start_hand()
        assert repository.calls == 2
        assert game.peek_completed_hand() is None

    asyncio.run(run_test())