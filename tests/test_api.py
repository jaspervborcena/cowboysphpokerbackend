import asyncio
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main as api_main
from app.main import app, tables
from database import BuyInStatus, NullHandRepository, WalletTransactionStatus
from logic.connections import ConnectionManager
from logic.first_party_table import FirstPartyNLHGame
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
        self.in_play_balances: dict[str, int] = {}
        self.global_wallets: dict[str, dict[str, object]] = {}

    def recover_pending_settlements(self) -> int:
        return 0

    def ensure_global_wallet(self, user_id: str) -> tuple[dict[str, object], bool]:
        wallet = self.global_wallets.get(user_id)
        if wallet is not None:
            return wallet, False
        wallet = {
            "wallet_id": f"global-wallet-{user_id}",
            "wallet_type": "GLOBAL_PLAYMONEY",
            "currency_type": "GLOBAL_PLAYMONEY",
            "owner_user_id": user_id,
            "chip_balance": 20_000,
        }
        self.global_wallets[user_id] = wallet
        return wallet, True

    def get_global_wallet(self, user_id: str) -> dict[str, object] | None:
        return self.global_wallets.get(user_id)

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

    def get_club_member(self, club_id: str, user_id: str) -> dict[str, object] | None:
        if user_id not in self.balances:
            return None
        return {
            "status": "active",
            "role": self.roles.get(user_id, "owner"),
            "username": f"Name {user_id}",
            "chip_balance": self.balances[user_id],
            "in_play_balance": self.in_play_balances.get(user_id, 0),
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


def test_game_catalog_lists_only_first_party_supported_games() -> None:
    with TestClient(app) as client:
        response = client.get("/games")

    assert response.status_code == 200
    catalog = response.json()
    entries = catalog["games"]
    assert catalog["active_game_id"] == "no_limit_texas_holdem"
    assert entries == [
        {
            "game_id": "no_limit_texas_holdem",
            "display_name": "No Limit Texas Hold'em",
            "category": "Hold'em",
            "backend_status": "supported",
            "create_supported": True,
        }
    ]


def test_global_playmoney_wallet_is_provisioned_once_for_authenticated_user(monkeypatch) -> None:
    repository = FakeHandRepository({})
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        unauthorized = client.post("/me/wallets/global-playmoney/ensure")
        first = client.post(
            "/me/wallets/global-playmoney/ensure",
            headers={"Authorization": "Bearer valid:wallet-user"},
        )
        retry = client.post(
            "/me/wallets/global-playmoney/ensure",
            headers={"Authorization": "Bearer valid:wallet-user"},
        )
        wallet = client.get(
            "/me/wallets/global-playmoney",
            headers={"Authorization": "Bearer valid:wallet-user"},
        )
        another_user = client.get(
            "/me/wallets/global-playmoney",
            headers={"Authorization": "Bearer valid:other-user"},
        )

    assert unauthorized.status_code == 401
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["balance"] == 20_000
    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["balance"] == 20_000
    assert wallet.status_code == 200
    assert wallet.json()["user_id"] == "wallet-user"
    assert wallet.json()["currency_type"] == "GLOBAL_PLAYMONEY"
    assert another_user.status_code == 404


def test_member_wallet_summary_separates_available_and_in_play_chips(monkeypatch) -> None:
    repository = FakeHandRepository(
        {"wallet-player": 14_600},
        {"wallet-player": "PLAYER"},
    )
    repository.in_play_balances["wallet-player"] = 1_200
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.get(
            "/clubs/wallet-club/members/me/wallet",
            headers={"Authorization": "Bearer valid:wallet-player"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "club_id": "wallet-club",
        "user_id": "wallet-player",
        "role": "PLAYER",
        "currency_type": "CLUB_CHIPS",
        "available_balance": 14_600,
        "in_play_balance": 1_200,
    }


def test_live_nlh_entry_consent_and_straddle_options(monkeypatch) -> None:
    club_id = "nlh-options-club"
    table_id = "nlh-options-table"
    player_ids = ["option-player-0", "option-player-1", "option-player-2", "option-player-3"]
    balances = {"option-owner": 1000, **{player_id: 1000 for player_id in player_ids}}
    roles = {"option-owner": "MANAGER", **{player_id: "PLAYER" for player_id in player_ids}}
    roles["option-new-player"] = "PLAYER"
    balances["option-new-player"] = 1000
    repository = FakeHandRepository(balances, roles)
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "small_blind": 10,
                "big_blind": 20,
                "buy_in_min": 20,
                "buy_in_max": 500,
                "table_size": 6,
            },
            headers={"Authorization": "Bearer valid:option-owner"},
        )
        assert response.status_code == 201

        for seat_index, player_id in enumerate(player_ids):
            response = client.post(
                f"/clubs/{club_id}/tables/{table_id}/seats",
                json={
                    "seat_index": seat_index,
                    "buy_in_amount": 200,
                    "buy_in_id": f"00000000-0000-0000-0000-{seat_index + 101:012d}",
                },
                headers={"Authorization": f"Bearer valid:{player_id}"},
            )
            assert response.status_code == 201

        start_url = f"/clubs/{club_id}/tables/{table_id}/hands"
        owner_headers = {"Authorization": "Bearer valid:option-owner"}
        response = client.post(
            start_url,
            json={"straddle_player_id": "not-seated"},
            headers=owner_headers,
        )
        assert response.status_code == 400
        assert tables[(club_id, table_id)].game._button_offset == 0

        response = client.post(
            start_url,
            json={"straddle_player_id": player_ids[3]},
            headers=owner_headers,
        )
        assert response.status_code == 201
        game = tables[(club_id, table_id)].game
        straddler = next(
            player for player in game.poker_game.players
            if player.player_id == player_ids[3]
        )
        assert straddler.street_contribution == 40

        while game.is_hand_running:
            actor = game.current_actor_user_id
            assert actor is not None
            assert game.handle_player_action(actor, "FOLD")

        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}/seats",
            json={
                "seat_index": 4,
                "buy_in_amount": 200,
                "buy_in_id": "00000000-0000-0000-0000-000000000201",
                "accept_big_blind_post": True,
            },
            headers={"Authorization": "Bearer valid:option-new-player"},
        )
        assert response.status_code == 201
        assert game.seats[4].new_to_table is True
        assert game.seats[4].accept_big_blind_post is True

        response = client.post(start_url, headers=owner_headers)
        assert response.status_code == 201
        new_player = next(
            player for player in game.poker_game.players
            if player.player_id == "option-new-player"
        )
        assert new_player.total_contribution == 20
        assert game.seats[4].new_to_table is False
        assert game.seats[4].accept_big_blind_post is False


def test_table_size_cannot_exceed_server_maximum(monkeypatch) -> None:
    club_id = "table-size-limit-club"
    table_id = "oversized-table"
    repository = FakeHandRepository({"club-owner": 1000}, {"club-owner": "OWNER"})
    configure_test_dependencies(monkeypatch, repository)
    monkeypatch.setattr(api_main, "max_seats", 6)

    with TestClient(app) as client:
        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "small_blind": 1,
                "big_blind": 2,
                "buy_in_min": 50,
                "buy_in_max": 1000,
                "table_size": 7,
            },
            headers={"Authorization": "Bearer valid:club-owner"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == 40002
    assert (club_id, table_id) not in tables


def test_table_creation_rejects_game_not_supported_by_backend(monkeypatch) -> None:
    club_id = "unsupported-game-club"
    table_id = "unsupported-game-table"
    repository = FakeHandRepository({"club-owner": 1000}, {"club-owner": "OWNER"})
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        response = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "game_id": "pot_limit_omaha_holdem",
                "small_blind": 1,
                "big_blind": 2,
                "buy_in_min": 50,
                "buy_in_max": 1000,
            },
            headers={"Authorization": "Bearer valid:club-owner"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == 40002
    assert (club_id, table_id) not in tables


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
                "table_size": 4,
                "game_id": "no_limit_texas_holdem",
            },
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert response.status_code == 201
        assert response.json()["game_id"] == "no_limit_texas_holdem"
        assert tables[table_key].game.max_seats == 4

        listing = client.get(
            f"/clubs/{club_id}/tables",
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert listing.json()[0]["game_id"] == "no_limit_texas_holdem"

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
        assert response.json()["game_id"] == "no_limit_texas_holdem"
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
            assert actor_state["game_id"] == "no_limit_texas_holdem"
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


def test_missing_table_websocket_returns_standard_not_found_error() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/ws/table/missing-club/missing-table") as socket:
            error = socket.receive_json()
            assert error["code"] == 40401
            assert error["message"] == "Not found."
            assert "missing-table" not in str(error)
            with pytest.raises(WebSocketDisconnect) as disconnect:
                socket.receive_json()

    assert disconnect.value.code == 4404


def test_versioned_websocket_envelopes_preserve_auth_and_private_snapshots(monkeypatch) -> None:
    club_id = "envelope-test-club"
    table_id = "envelope-test-table"
    table_key = (club_id, table_id)
    topic = f"clubs/{club_id}/tables/{table_id}/game"
    repository = FakeHandRepository(
        {"club-owner": 1000, "envelope-player-a": 1000, "envelope-player-b": 1000},
        {
            "club-owner": "OWNER",
            "envelope-player-a": "PLAYER",
            "envelope-player-b": "PLAYER",
        },
    )
    configure_test_dependencies(monkeypatch, repository)

    with TestClient(app) as client:
        created = client.post(
            f"/clubs/{club_id}/tables/{table_id}",
            json={
                "small_blind": 10,
                "big_blind": 20,
                "buy_in_min": 20,
                "buy_in_max": 1000,
            },
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert created.status_code == 201
        for seat_index, user_id in enumerate(("envelope-player-a", "envelope-player-b")):
            seated = client.post(
                f"/clubs/{club_id}/tables/{table_id}/seats",
                json={
                    "seat_index": seat_index,
                    "buy_in_amount": 500,
                    "buy_in_id": f"00000000-0000-0000-0000-{seat_index + 30:012d}",
                },
                headers={"Authorization": f"Bearer valid:{user_id}"},
            )
            assert seated.status_code == 201
        started = client.post(
            f"/clubs/{club_id}/tables/{table_id}/hands",
            headers={"Authorization": "Bearer valid:club-owner"},
        )
        assert started.status_code == 201

        runtime = tables[table_key]
        actor_user_id = runtime.game.current_actor_user_id
        assert actor_user_id is not None
        other_user_id = next(
            user_id
            for user_id in ("envelope-player-a", "envelope-player-b")
            if user_id != actor_user_id
        )

        with client.websocket_connect(f"/ws/table/{club_id}/{table_id}") as malformed_socket:
            malformed_socket.send_text("{invalid-json")
            malformed_error = malformed_socket.receive_json()
            assert malformed_error["code"] == 40002
            assert malformed_error["message"] == "Invalid input provided."
            assert "invalid-json" not in str(malformed_error)
            with pytest.raises(WebSocketDisconnect) as malformed_disconnect:
                malformed_socket.receive_json()
        assert malformed_disconnect.value.code == 4400

        with client.websocket_connect(f"/ws/table/{club_id}/{table_id}") as expired_socket:
            expired_socket.send_json(
                {
                    "version": "1.0",
                    "topic": topic,
                    "event": "AUTHENTICATE",
                    "correlation_id": "auth-expired",
                    "payload": {"id_token": "expired-token"},
                }
            )
            expired_error = expired_socket.receive_json()
            assert expired_error["code"] == 40101
            assert expired_error["message"] == "Authentication required."
            assert "invalid token" not in str(expired_error).lower()
            with pytest.raises(WebSocketDisconnect) as expired_disconnect:
                expired_socket.receive_json()
        assert expired_disconnect.value.code == 4401

        with client.websocket_connect(f"/ws/table/{club_id}/{table_id}") as invalid_socket:
            invalid_socket.send_json(
                {
                    "version": "1.0",
                    "topic": "clubs/other-club/tables/other-table/game",
                    "event": "AUTHENTICATE",
                    "correlation_id": "wrong-topic",
                    "payload": {"id_token": f"valid:{actor_user_id}"},
                }
            )
            auth_error = invalid_socket.receive_json()
            assert auth_error["code"] == 40002
            assert auth_error["message"] == "Invalid input provided."
            assert "topic does not match" not in str(auth_error)
            assert auth_error["trace_id"].startswith("err_")
            with pytest.raises(WebSocketDisconnect) as disconnect:
                invalid_socket.receive_json()
        assert disconnect.value.code == 4401

        with client.websocket_connect(f"/ws/table/{club_id}/{table_id}") as actor_socket:
            actor_socket.send_json(
                {
                    "version": "1.0",
                    "topic": topic,
                    "event": "AUTHENTICATE",
                    "correlation_id": "auth-actor",
                    "payload": {"id_token": f"valid:{actor_user_id}"},
                }
            )
            authenticated = actor_socket.receive_json()
            actor_state_envelope = actor_socket.receive_json()
            assert authenticated["event"] == "AUTHENTICATED"
            assert authenticated["payload"]["user_id"] == actor_user_id
            assert authenticated["correlation_id"] == "auth-actor"
            assert authenticated["topic"] == topic
            assert actor_state_envelope["event"] == "TABLE_SNAPSHOT"
            assert actor_state_envelope["version"] == "1.0"
            assert actor_state_envelope["timestamp"]
            actor_state = actor_state_envelope["payload"]
            actor_cards = next(
                player["hole_cards"]
                for player in actor_state["players"]
                if player["user_id"] == actor_user_id
            )
            assert len(actor_cards) == 2

            with client.websocket_connect(f"/ws/table/{club_id}/{table_id}") as other_socket:
                other_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "AUTHENTICATE",
                        "correlation_id": "auth-other",
                        "payload": {"id_token": f"valid:{other_user_id}"},
                    }
                )
                other_socket.receive_json()
                other_state_envelope = other_socket.receive_json()
                other_state = other_state_envelope["payload"]
                hidden_actor = next(
                    player
                    for player in other_state["players"]
                    if player["user_id"] == actor_user_id
                )
                assert hidden_actor["hole_cards"] == ["??", "??"]

                other_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "PRE_ACTION",
                        "correlation_id": "pre-action-1",
                        "payload": {"action": "CHECK_FOLD"},
                    }
                )
                pre_action_ack = other_socket.receive_json()
                assert pre_action_ack["event"] == "PRE_ACTION_SET"
                assert pre_action_ack["correlation_id"] == "pre-action-1"
                assert pre_action_ack["payload"]["action"] == "CHECK_FOLD"

                action = "CALL" if (runtime.game.current_call_amount or 0) > 0 else "CHECK"
                actor_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "PLAYER_ACTION",
                        "correlation_id": "action-1",
                        "payload": {"action": action, "action_id": "action-id-1"},
                    }
                )
                actor_snapshot = actor_socket.receive_json()
                other_snapshot = other_socket.receive_json()
                assert actor_snapshot["event"] == "TABLE_SNAPSHOT"
                assert other_snapshot["event"] == "TABLE_SNAPSHOT"
                assert actor_snapshot["payload"]["pre_action"] is None
                assert other_snapshot["payload"]["pre_action"] is None
                replay_frame_count = len(runtime.game._replay_frames)

                actor_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "PLAYER_ACTION",
                        "correlation_id": "action-retry",
                        "payload": {"action": action, "action_id": "action-id-1"},
                    }
                )
                retry_snapshot = actor_socket.receive_json()
                other_retry_snapshot = other_socket.receive_json()
                assert retry_snapshot["event"] == "TABLE_SNAPSHOT"
                assert other_retry_snapshot["event"] == "TABLE_SNAPSHOT"
                duplicate_ack = actor_socket.receive_json()
                assert duplicate_ack["event"] == "ACTION_ACKNOWLEDGED"
                assert duplicate_ack["payload"] == {
                    "action_id": "action-id-1",
                    "duplicate": True,
                }
                assert len(runtime.game._replay_frames) == replay_frame_count

                actor_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "PLAYER_ACTION",
                        "correlation_id": "action-id-conflict",
                        "payload": {"action": "FOLD", "action_id": "action-id-1"},
                    }
                )
                conflict = actor_socket.receive_json()
                assert conflict["event"] == "ACTION_ERROR"
                assert conflict["payload"]["code"] == 40902
                assert "different action details" not in str(conflict)

                actor_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "PLAYER_ACTION",
                        "correlation_id": "invalid-action-1",
                        "payload": {
                            "action": "NOT_A_POKER_ACTION",
                            "action_id": "invalid-action-id",
                        },
                    }
                )
                action_error = actor_socket.receive_json()
                assert action_error["event"] == "ACTION_ERROR"
                assert action_error["correlation_id"] == "invalid-action-1"
                assert action_error["payload"]["code"] == 40901
                assert "NOT_A_POKER_ACTION" not in str(action_error)

        monkeypatch.setattr(api_main, "websocket_heartbeat_interval_seconds", 1)
        monkeypatch.setattr(api_main, "websocket_idle_timeout_seconds", 2)
        with client.websocket_connect(f"/ws/table/{club_id}/{table_id}") as idle_socket:
            idle_socket.send_json(
                {
                    "version": "1.0",
                    "topic": topic,
                    "event": "AUTHENTICATE",
                    "correlation_id": "auth-idle",
                    "payload": {"id_token": f"valid:{other_user_id}"},
                }
            )
            idle_socket.receive_json()
            idle_socket.receive_json()
            heartbeat = idle_socket.receive_json()
            assert heartbeat["event"] == "PING"
            idle_error = idle_socket.receive_json()
            assert idle_error["code"] == 50001
            assert idle_error["message"] == "Service temporarily unavailable."
            with pytest.raises(WebSocketDisconnect) as idle_disconnect:
                idle_socket.receive_json()
            assert idle_disconnect.value.code == 4408

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
        "table_size": 6,
        "game_id": "no_limit_texas_holdem",
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
        assert created.json()["game_id"] == "no_limit_texas_holdem"
        assert tables[table_key].game.max_seats == 6
        assert repository.union_table_records[(union_id, table_id)]["max_seats"] == 6
        assert repository.union_table_records[(union_id, table_id)]["game_id"] == "no_limit_texas_holdem"
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
        assert tables_response.json()[0]["game_id"] == "no_limit_texas_holdem"
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

        topic = f"unions/{union_id}/tables/{table_id}/game"
        with client.websocket_connect(f"/ws/union/{union_id}/{table_id}") as socket_a:
            socket_a.send_json(
                {
                    "version": "1.0",
                    "topic": topic,
                    "event": "AUTHENTICATE",
                    "correlation_id": "union-auth-a",
                    "payload": {
                        "id_token": "valid:union-player-a",
                        "club_id": "club-a",
                    },
                }
            )
            assert socket_a.receive_json()["event"] == "AUTHENTICATED"
            initial_a = socket_a.receive_json()
            assert initial_a["event"] == "TABLE_SNAPSHOT"
            assert initial_a["payload"]["is_hand_running"] is False
            assert {player["club_id"] for player in initial_a["payload"]["players"]} == {"club-a", "club-b"}

            with client.websocket_connect(f"/ws/union/{union_id}/{table_id}") as socket_b:
                socket_b.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "AUTHENTICATE",
                        "correlation_id": "union-auth-b",
                        "payload": {
                            "id_token": "valid:union-player-b",
                            "club_id": "club-b",
                        },
                    }
                )
                assert socket_b.receive_json()["event"] == "AUTHENTICATED"
                initial_b = socket_b.receive_json()
                assert initial_b["event"] == "TABLE_SNAPSHOT"
                assert initial_b["payload"]["is_hand_running"] is False

                start = client.post(
                    f"/unions/{union_id}/tables/{table_id}/hands",
                    headers={"Authorization": "Bearer valid:union-admin"},
                )
                assert start.status_code == 201
                started_a = socket_a.receive_json()
                started_b = socket_b.receive_json()
                assert started_a["event"] == started_b["event"] == "TABLE_SNAPSHOT"
                state_a = started_a["payload"]
                state_b = started_b["payload"]
                assert state_a["is_hand_running"] is True
                assert state_b["is_hand_running"] is True
                assert {player["club_id"] for player in state_a["players"]} == {"club-a", "club-b"}
                assert {player["club_id"] for player in state_b["players"]} == {"club-a", "club-b"}
                player_a_view = next(player for player in state_a["players"] if player["user_id"] == "union-player-a")
                player_b_view = next(player for player in state_a["players"] if player["user_id"] == "union-player-b")
                assert player_a_view["hole_cards"] != ["??", "??"]
                assert player_b_view["hole_cards"] == ["??", "??"]
                player_a_other_view = next(player for player in state_b["players"] if player["user_id"] == "union-player-a")
                player_b_other_view = next(player for player in state_b["players"] if player["user_id"] == "union-player-b")
                assert player_a_other_view["hole_cards"] == ["??", "??"]
                assert player_b_other_view["hole_cards"] != ["??", "??"]

                actor_user_id = tables[table_key].game.current_actor_user_id
                selected_socket = socket_a if actor_user_id == "union-player-a" else socket_b
                other_socket = socket_b if selected_socket is socket_a else socket_a
                selected_socket.send_json(
                    {
                        "version": "1.0",
                        "topic": topic,
                        "event": "PLAYER_ACTION",
                        "correlation_id": "union-action-1",
                        "payload": {"action": "FOLD", "action_id": "union-action-id-1"},
                    }
                )
                selected_result = selected_socket.receive_json()
                other_result = other_socket.receive_json()
                assert selected_result["event"] == other_result["event"] == "TABLE_SNAPSHOT"
                assert selected_result["payload"]["is_hand_running"] is False
                assert other_result["payload"]["is_hand_running"] is False

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
        assert old_request.status_code == 400
        assert old_request.json()["code"] == 40001
        assert old_request.json()["message"] == "Invalid input provided."
        assert old_request.json()["trace_id"].startswith("err_")

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
        assert response.json()["code"] == 40901
        assert response.json()["message"] == "Request conflict."
        assert "Insufficient club chip balance" not in str(response.json())
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
    for response in (missing, invalid):
        assert response.json()["code"] == 40101
        assert response.json()["message"] == "Authentication required."
        assert "detail" not in response.json()


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
    assert response.json()["code"] == 40301


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
        game = FirstPartyNLHGame("retry-table", 10, 20, 20, 1000)
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


def test_worker_routes_fail_closed_without_service_token(monkeypatch) -> None:
    monkeypatch.delenv("WORKER_COORDINATION_TOKEN", raising=False)

    with TestClient(app) as client:
        response = client.post("/workers/worker-auth-test/start")

    assert response.status_code == 503


def test_worker_routes_require_valid_service_token_and_reject_forced_reclaim(monkeypatch) -> None:
    token = "test-worker-coordination-secret"
    monkeypatch.setenv("WORKER_COORDINATION_TOKEN", token)

    with TestClient(app) as client:
        missing_token = client.get("/workers/worker-auth-test/tables")
        invalid_token = client.post(
            "/workers/worker-auth-test/start",
            headers={"Authorization": "Bearer wrong-token"},
        )
        started = client.post(
            "/workers/worker-auth-test/start",
            headers={"Authorization": f"Bearer {token}"},
        )
        claimed = client.post(
            "/workers/worker-auth-test/tables/table-auth-test/lease",
            json={"worker_id": "worker-auth-test", "force_reclaim": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        forced_reclaim = client.post(
            "/workers/worker-auth-test/tables/table-auth-test/lease",
            json={"worker_id": "worker-auth-test", "force_reclaim": True},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert missing_token.status_code == 401
    assert invalid_token.status_code == 401
    assert started.status_code == 200
    assert claimed.status_code == 200
    assert forced_reclaim.status_code == 403


def test_worker_routes_reject_mismatched_worker_id(monkeypatch) -> None:
    token = "test-worker-coordination-secret"
    monkeypatch.setenv("WORKER_COORDINATION_TOKEN", token)

    with TestClient(app) as client:
        response = client.post(
            "/workers/worker-a/tables/table-auth-test/lease",
            json={"worker_id": "worker-b", "force_reclaim": False},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["code"] == 40002
    assert payload["errors"][0]["message"] == "One or more fields have an invalid format."