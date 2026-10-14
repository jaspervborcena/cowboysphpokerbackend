from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

from google.api_core.exceptions import GoogleAPICallError
from fastapi import Depends, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from app.api.hand_history import router as hand_history_router
from app.auth.roles import (
    AuthenticatedUser,
    ClubMembership,
    ClubRole,
    UnionMembership,
    UnionRole,
    get_active_club_member,
    get_active_union_admin,
    get_active_union_club_member,
    get_current_user,
    load_active_club_member,
    load_active_union_club_member,
    require_club_role,
    require_union_admin,
    verify_id_token,
)
from base_poker_game import BasePokerGame
from database import (
    BuyInStatus,
    FirestoreRequiredError,
    INITIAL_CLUB_TREASURY_FUNDS,
    WalletTransactionStatus,
    create_hand_repository,
)
from logic.auth import FirebaseTokenVerifier
from logic.connections import ConnectionManager
from logic.runtime import TableRuntime


class CreateTableRequest(BaseModel):
    small_blind: int = Field(gt=0)
    big_blind: int = Field(gt=0)
    buy_in_min: int = Field(gt=0)
    buy_in_max: int = Field(gt=0)


class SeatPlayerRequest(BaseModel):
    seat_index: int = Field(ge=0)
    buy_in_amount: int = Field(gt=0, strict=True)
    buy_in_id: UUID


class ClubWalletTransferRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    amount: int = Field(gt=0, strict=True)
    transaction_id: UUID
    note: str = Field(default="", max_length=500)


class ClubTreasuryTransferRequest(BaseModel):
    destination_club_id: str = Field(min_length=1, max_length=128)
    amount: int = Field(gt=0, strict=True)
    transaction_id: UUID
    note: str = Field(default="", max_length=500)


class CreateClubRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    owner_user_id: str = Field(min_length=1, max_length=128)


class CreateUnionTableRequest(CreateTableRequest):
    origin_club_id: str = Field(min_length=1)
    table_name: str = Field(min_length=1, max_length=100)


class UpdateTableSettingsRequest(BaseModel):
    jackpot_enabled: bool | None = None
    jackpot_fee: int | None = Field(default=None, ge=0)
    jackpot_min_pot: int | None = Field(default=None, ge=0)
    jackpot_qualifying_hand: str | None = Field(default=None, min_length=1, max_length=80)
    rake_rate: float | None = Field(default=None, ge=0, le=0.1)
    rake_cap: int | None = Field(default=None, ge=0)
    rake_min_pot: int | None = Field(default=None, ge=0)
    no_flop_no_drop: bool | None = None
    hand_of_day_enabled: bool | None = None
    hand_of_day_target: int | None = Field(default=None, ge=0)
    hand_of_day_prize: int | None = Field(default=None, ge=0)


class UnionAdminProvisionRequest(BaseModel):
    role: UnionRole = UnionRole.UNION_ADMIN
    status: str = Field(default="active", min_length=1, max_length=32)


class UnionClubProvisionRequest(BaseModel):
    union_name: str = Field(min_length=1, max_length=200)
    status: str = Field(default="active", min_length=1, max_length=32)


class PlayerAction(BaseModel):
    action: str
    amount: int | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(repository.recover_pending_settlements)
    yield


app = FastAPI(title="Cowboys PH Poker Game Server", lifespan=lifespan)
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:4200,http://127.0.0.1:4200",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(hand_history_router)
connections = ConnectionManager()
repository = create_hand_repository(os.getenv("FIRESTORE_ENABLED", "false").lower() == "true")
app.state.repository = repository
app.state.token_verifier = FirebaseTokenVerifier(
    os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
)
tables: dict[tuple[str, str], TableRuntime] = {}
max_seats = int(os.getenv("MAX_SEATS", "9"))
turn_timeout_seconds = int(os.getenv("TURN_TIMEOUT_SECONDS", "30"))


def get_table(club_id: str, table_id: str) -> TableRuntime:
    runtime = tables.get((club_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Table not found.")
    return runtime


def union_table_key(union_id: str, table_id: str) -> tuple[str, str]:
    return (f"union:{union_id}", table_id)


def table_lobby_entry(game: BasePokerGame) -> dict[str, Any]:
    return {
        "table_id": game.table_id,
        "table_name": game.table_name,
        "is_union_table": game.union_id is not None,
        "union_id": game.union_id,
        "origin_club_id": game.origin_club_id,
        "small_blind": game.small_blind,
        "big_blind": game.big_blind,
        "buy_in_min": game.buy_in_min,
        "buy_in_max": game.buy_in_max,
        "max_seats": game.max_seats,
        "seated_count": len(game.seats),
        "is_hand_running": game.is_hand_running,
        **game.table_settings.as_dict(),
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/clubs/{club_id}/members/me")
async def get_my_club_membership(
    club_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, str]:
    profile = membership.profile
    return {
        "club_id": club_id,
        "user_id": membership.user.uid,
        "username": str(
            profile.get("username")
            or membership.user.display_name
            or membership.user.uid
        ),
        "role": membership.role.value,
    }


def wallet_transaction_http_error(status: WalletTransactionStatus) -> HTTPException:
    if status is WalletTransactionStatus.CLUB_NOT_FOUND:
        return HTTPException(status_code=404, detail="Source or destination club not found.")
    if status is WalletTransactionStatus.SAME_CLUB:
        return HTTPException(status_code=422, detail="A club cannot transfer chips to itself.")
    if status is WalletTransactionStatus.MEMBER_NOT_FOUND:
        return HTTPException(status_code=404, detail="Club member account not found.")
    if status is WalletTransactionStatus.UNAUTHORIZED_OPERATOR:
        return HTTPException(status_code=403, detail="Only club owners and managers can transfer treasury funds.")
    if status is WalletTransactionStatus.UNAUTHORIZED_DOWNLINE:
        return HTTPException(status_code=403, detail="The operator is not authorized for this member.")
    if status is WalletTransactionStatus.PLAYER_HAS_CHIPS_IN_PLAY:
        return HTTPException(status_code=409, detail="Claim back is unavailable while the member has chips in play.")
    if status is WalletTransactionStatus.INSUFFICIENT_BALANCE:
        return HTTPException(status_code=409, detail="Insufficient available club chip balance.")
    if status is WalletTransactionStatus.INVALID_BALANCE:
        return HTTPException(status_code=409, detail="Club wallet balance is invalid.")
    if status is WalletTransactionStatus.INVALID_AMOUNT:
        return HTTPException(status_code=422, detail="Transfer amount must be a positive integer.")
    if status is WalletTransactionStatus.IDEMPOTENCY_CONFLICT:
        return HTTPException(status_code=409, detail="transaction_id was already used for a different transfer.")
    return HTTPException(status_code=500, detail="Club wallet transaction could not be completed.")


@app.post("/platform/clubs", status_code=201)
async def create_club(
    request: CreateClubRequest,
    platform_admin: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    if not platform_admin.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access is required.")

    club_id = str(uuid4())
    try:
        created = await asyncio.to_thread(
            repository.create_club,
            club_id,
            request.name,
            request.owner_user_id,
            platform_admin.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club provisioning is unavailable.") from error
    if not created:
        raise HTTPException(status_code=409, detail="Club ID already exists; retry the request.")
    return {
        "club_id": club_id,
        "name": request.name,
        "owner_user_id": request.owner_user_id,
        "treasury_balance": INITIAL_CLUB_TREASURY_FUNDS,
    }


@app.get("/clubs/{club_id}/wallet")
async def get_club_wallet(
    club_id: str,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    try:
        balance = await asyncio.to_thread(repository.get_club_wallet_balance, club_id)
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club wallet is unavailable.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="Club wallet balance is invalid.") from error
    return {"club_id": club_id, "currency_type": "CLUB_CHIPS", "chip_balance": balance}


@app.post("/clubs/{club_id}/wallet/transfers")
async def transfer_between_club_wallets(
    club_id: str,
    request: ClubTreasuryTransferRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    operator_id = membership.user.uid
    try:
        status = await asyncio.to_thread(
            repository.transfer_between_club_wallets,
            club_id,
            request.destination_club_id,
            operator_id,
            request.amount,
            str(request.transaction_id),
            operator_id,
            request.note,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club wallet is unavailable.") from error
    if status not in {
        WalletTransactionStatus.COMPLETED,
        WalletTransactionStatus.IDEMPOTENT_REPLAY,
    }:
        raise wallet_transaction_http_error(status)
    return {
        "transaction_id": str(request.transaction_id),
        "type": "INTER_CLUB_TRANSFER",
        "from_club_id": club_id,
        "to_club_id": request.destination_club_id,
        "amount": request.amount,
        "status": status.value,
    }


@app.post("/clubs/{club_id}/wallet/send-out")
async def send_out_club_chips(
    club_id: str,
    request: ClubWalletTransferRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER, ClubRole.AGENT])
    ),
) -> dict[str, Any]:
    operator_id = membership.user.uid
    try:
        status = await asyncio.to_thread(
            repository.send_out_club_chips,
            club_id,
            operator_id,
            request.user_id,
            request.amount,
            str(request.transaction_id),
            operator_id,
            request.note,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club wallet is unavailable.") from error
    if status not in {
        WalletTransactionStatus.COMPLETED,
        WalletTransactionStatus.IDEMPOTENT_REPLAY,
    }:
        raise wallet_transaction_http_error(status)
    return {
        "club_id": club_id,
        "transaction_id": str(request.transaction_id),
        "type": "SEND_OUT",
        "amount": request.amount,
        "from_user_id": operator_id,
        "to_user_id": request.user_id,
        "status": status.value,
    }


@app.post("/clubs/{club_id}/wallet/claim-back")
async def claim_back_club_chips(
    club_id: str,
    request: ClubWalletTransferRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER, ClubRole.AGENT])
    ),
) -> dict[str, Any]:
    operator_id = membership.user.uid
    try:
        status = await asyncio.to_thread(
            repository.claim_back_club_chips,
            club_id,
            request.user_id,
            operator_id,
            request.amount,
            str(request.transaction_id),
            operator_id,
            request.note,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club wallet is unavailable.") from error
    if status not in {
        WalletTransactionStatus.COMPLETED,
        WalletTransactionStatus.IDEMPOTENT_REPLAY,
    }:
        raise wallet_transaction_http_error(status)
    return {
        "club_id": club_id,
        "transaction_id": str(request.transaction_id),
        "type": "CLAIM_BACK",
        "amount": request.amount,
        "from_user_id": request.user_id,
        "to_user_id": operator_id,
        "status": status.value,
    }


@app.get("/clubs/{club_id}/tables")
async def list_club_tables(
    club_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> list[dict[str, Any]]:
    return [
        table_lobby_entry(runtime.game)
        for (scope_id, _), runtime in tables.items()
        if scope_id == club_id and runtime.union_id is None
    ]


@app.get("/clubs/{club_id}/tables/{table_id}")
async def get_club_table(
    club_id: str,
    table_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, Any]:
    runtime = get_table(club_id, table_id)
    return table_lobby_entry(runtime.game)


@app.get("/clubs/{club_id}/unions")
async def list_club_unions(
    club_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> list[dict[str, str]]:
    try:
        return await asyncio.to_thread(repository.get_unions_for_club, club_id)
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club union list is unavailable.") from error


@app.get("/clubs/{club_id}/unions/{union_id}/tables")
async def list_union_tables(
    club_id: str,
    union_id: str,
    membership: ClubMembership = Depends(get_active_union_club_member),
) -> list[dict[str, Any]]:
    scope_id, _ = union_table_key(union_id, "")
    return [
        table_lobby_entry(runtime.game)
        for (table_scope, _), runtime in tables.items()
        if table_scope == scope_id
    ]


@app.get("/unions/{union_id}/tables/{table_id}")
async def get_union_table(
    union_id: str,
    table_id: str,
    membership: ClubMembership = Depends(get_active_union_club_member),
) -> dict[str, Any]:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    return table_lobby_entry(runtime.game)


@app.get("/unions/{union_id}/membership/me")
async def get_my_union_membership(
    union_id: str,
    membership=Depends(get_active_union_admin),
) -> dict[str, str]:
    return {
        "union_id": union_id,
        "user_id": membership.user.uid,
        "role": membership.role.value,
    }


@app.post("/unions/{union_id}/admins/{user_id}", status_code=201)
async def provision_union_admin(
    union_id: str,
    user_id: str,
    request: UnionAdminProvisionRequest,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, str]:
    if request.role is not UnionRole.UNION_ADMIN:
        raise HTTPException(
            status_code=400,
            detail="Only UNION_ADMIN provisioning is supported for union administrators.",
        )
    try:
        await asyncio.to_thread(
            repository.set_union_admin,
            union_id,
            user_id,
            request.role.value,
            request.status,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union administrator could not be provisioned.") from error
    return {
        "union_id": union_id,
        "user_id": user_id,
        "role": request.role.value,
        "status": request.status,
    }


@app.post("/unions/{union_id}/clubs/{club_id}", status_code=201)
async def provision_union_club(
    union_id: str,
    club_id: str,
    request: UnionClubProvisionRequest,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, str]:
    club_payload = {
        "union_id": union_id,
        "club_id": club_id,
        "union_name": request.union_name,
        "status": request.status,
    }
    try:
        await asyncio.to_thread(
            repository.add_union_club,
            union_id,
            club_id,
            club_payload,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union club membership could not be provisioned.") from error
    return {
        "union_id": union_id,
        "club_id": club_id,
        "union_name": request.union_name,
        "status": request.status,
    }


@app.patch("/clubs/{club_id}/tables/{table_id}/settings")
async def update_club_table_settings(
    club_id: str,
    table_id: str,
    request: UpdateTableSettingsRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    runtime = get_table(club_id, table_id)
    payload = runtime.game.update_table_settings(
        jackpot_enabled=request.jackpot_enabled,
        jackpot_fee=request.jackpot_fee,
        jackpot_min_pot=request.jackpot_min_pot,
        jackpot_qualifying_hand=request.jackpot_qualifying_hand,
        rake_rate=request.rake_rate,
        rake_cap=request.rake_cap,
        rake_min_pot=request.rake_min_pot,
        no_flop_no_drop=request.no_flop_no_drop,
        hand_of_day_enabled=request.hand_of_day_enabled,
        hand_of_day_target=request.hand_of_day_target,
        hand_of_day_prize=request.hand_of_day_prize,
    )
    return {
        "table_id": table_id,
        "table_name": runtime.game.table_name,
        **payload,
    }


@app.patch("/unions/{union_id}/tables/{table_id}/settings")
async def update_union_table_settings(
    union_id: str,
    table_id: str,
    request: UpdateTableSettingsRequest,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, Any]:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    payload = runtime.game.update_table_settings(
        jackpot_enabled=request.jackpot_enabled,
        jackpot_fee=request.jackpot_fee,
        jackpot_min_pot=request.jackpot_min_pot,
        jackpot_qualifying_hand=request.jackpot_qualifying_hand,
        rake_rate=request.rake_rate,
        rake_cap=request.rake_cap,
        rake_min_pot=request.rake_min_pot,
        no_flop_no_drop=request.no_flop_no_drop,
        hand_of_day_enabled=request.hand_of_day_enabled,
        hand_of_day_target=request.hand_of_day_target,
        hand_of_day_prize=request.hand_of_day_prize,
    )
    return {
        "union_id": union_id,
        "table_id": table_id,
        "table_name": runtime.game.table_name,
        **payload,
    }


@app.post("/clubs/{club_id}/tables/{table_id}", status_code=201)
async def create_table(
    club_id: str,
    table_id: str,
    request: CreateTableRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, str]:
    key = (club_id, table_id)
    if key in tables:
        raise HTTPException(status_code=409, detail="Table already exists.")
    try:
        game = BasePokerGame(
            table_id,
            request.small_blind,
            request.big_blind,
            request.buy_in_min,
            request.buy_in_max,
            max_seats,
            origin_club_id=club_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    tables[key] = TableRuntime(
        club_id, game, connections, repository, turn_timeout_seconds
    )
    return {"club_id": club_id, "table_id": table_id, "status": "created"}


@app.post("/unions/{union_id}/tables/{table_id}", status_code=201)
async def create_union_table(
    union_id: str,
    table_id: str,
    request: CreateUnionTableRequest,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, str]:
    key = union_table_key(union_id, table_id)
    if key in tables:
        raise HTTPException(status_code=409, detail="Union table already exists.")
    try:
        is_origin_club = await asyncio.to_thread(
            repository.is_club_in_union,
            union_id,
            request.origin_club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union club lookup is unavailable.") from error
    if not is_origin_club:
        raise HTTPException(status_code=403, detail="The origin club is not an active union member.")

    try:
        game = BasePokerGame(
            table_id,
            request.small_blind,
            request.big_blind,
            request.buy_in_min,
            request.buy_in_max,
            max_seats,
            union_id=union_id,
            origin_club_id=request.origin_club_id,
            table_name=request.table_name,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        table_created = await asyncio.to_thread(
            repository.create_union_table_record,
            union_id,
            table_id,
            {
                "union_id": union_id,
                "table_id": table_id,
                "table_name": request.table_name,
                "origin_club_id": request.origin_club_id,
                "small_blind": request.small_blind,
                "big_blind": request.big_blind,
                "buy_in_min": request.buy_in_min,
                "buy_in_max": request.buy_in_max,
                "max_seats": max_seats,
                "status": "open",
            },
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union table could not be persisted.") from error
    if not table_created:
        raise HTTPException(status_code=409, detail="Union table already exists.")
    tables[key] = TableRuntime(
        request.origin_club_id,
        game,
        connections,
        repository,
        turn_timeout_seconds,
        union_id=union_id,
    )
    return {
        "union_id": union_id,
        "table_id": table_id,
        "origin_club_id": request.origin_club_id,
        "status": "created",
    }


@app.delete("/unions/{union_id}/tables/{table_id}", status_code=204)
async def delete_union_table(
    union_id: str,
    table_id: str,
    membership: UnionMembership = Depends(require_union_admin()),
) -> Response:
    key = union_table_key(union_id, table_id)
    runtime = tables.get(key)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    if runtime.game.is_hand_running or runtime.game.seats:
        raise HTTPException(
            status_code=409,
            detail="Only empty union tables can be deleted; seated chips must be settled first.",
        )
    try:
        has_persisted_seats = await asyncio.to_thread(
            repository.has_union_table_seats,
            union_id,
            table_id,
        )
        if has_persisted_seats:
            raise HTTPException(
                status_code=409,
                detail="Union table has persisted seats; settle or cash out players before deletion.",
            )
        table_closed = await asyncio.to_thread(
            repository.close_union_table_record,
            union_id,
            table_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union table could not be closed.") from error
    if not table_closed:
        raise HTTPException(status_code=404, detail="Union table metadata not found.")
    tables.pop(key, None)
    return Response(status_code=204)


@app.post("/clubs/{club_id}/tables/{table_id}/seats", status_code=201)
async def seat_player(
    club_id: str,
    table_id: str,
    request: SeatPlayerRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.PLAYER])
    ),
) -> dict[str, Any]:
    runtime = get_table(club_id, table_id)
    member = membership.profile
    user_id = membership.user.uid
    username = str(member.get("username") or membership.user.display_name or user_id)
    async with runtime.lock:
        buy_in_id = str(request.buy_in_id)
        current_player = runtime.game.seats.get(request.seat_index)
        if current_player is not None:
            if (
                current_player.user_id == user_id
                and current_player.buy_in_id == buy_in_id
                and current_player.buy_in_amount == request.buy_in_amount
            ):
                return runtime.game.get_masked_state_for_player(user_id)
            raise HTTPException(status_code=409, detail="Seat is already occupied.")

        if not runtime.game.can_add_player(
            request.seat_index,
            user_id,
            username,
            request.buy_in_amount,
        ):
            raise HTTPException(status_code=409, detail="Seat or buy-in amount is invalid, or the player is already seated.")

        try:
            buy_in_status = await asyncio.to_thread(
                runtime.repository.reserve_buy_in,
                club_id,
                table_id,
                request.seat_index,
                user_id,
                username,
                request.buy_in_amount,
                buy_in_id,
            )
        except FirestoreRequiredError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except GoogleAPICallError as error:
            raise HTTPException(
                status_code=503,
                detail="Firestore could not confirm the buy-in. Retry using the same buy_in_id.",
            ) from error

        if buy_in_status == BuyInStatus.MEMBER_NOT_FOUND:
            raise HTTPException(status_code=404, detail="Club member account not found.")
        if buy_in_status == BuyInStatus.INSUFFICIENT_BALANCE:
            raise HTTPException(status_code=409, detail="Insufficient club chip balance.")
        if buy_in_status == BuyInStatus.INVALID_BALANCE:
            raise HTTPException(status_code=409, detail="Club member chip balance is invalid.")
        if buy_in_status == BuyInStatus.SEAT_OCCUPIED:
            raise HTTPException(status_code=409, detail="Seat is already reserved.")
        if buy_in_status == BuyInStatus.IDEMPOTENCY_CONFLICT:
            raise HTTPException(status_code=409, detail="buy_in_id was already used for a different reservation.")

        seated = runtime.game.add_player(
            request.seat_index,
            user_id,
            username,
            request.buy_in_amount,
            buy_in_id,
            club_id=club_id,
        )
        if not seated:
            raise HTTPException(
                status_code=500,
                detail="Buy-in was reserved but the in-memory seat could not be assigned. Retry with the same buy_in_id.",
            )
        return runtime.game.get_masked_state_for_player(user_id)


@app.post("/unions/{union_id}/tables/{table_id}/clubs/{club_id}/seats", status_code=201)
async def seat_union_player(
    union_id: str,
    table_id: str,
    club_id: str,
    request: SeatPlayerRequest,
    membership: ClubMembership = Depends(get_active_union_club_member),
) -> dict[str, Any]:
    if membership.role is not ClubRole.PLAYER:
        raise HTTPException(status_code=403, detail="PLAYER role is required to take a union seat.")
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")

    user_id = membership.user.uid
    member = membership.profile
    username = str(member.get("username") or membership.user.display_name or user_id)
    async with runtime.lock:
        buy_in_id = str(request.buy_in_id)
        current_player = runtime.game.seats.get(request.seat_index)
        if current_player is not None:
            if (
                current_player.user_id == user_id
                and current_player.club_id == club_id
                and current_player.buy_in_id == buy_in_id
                and current_player.buy_in_amount == request.buy_in_amount
            ):
                return runtime.game.get_masked_state_for_player(user_id)
            raise HTTPException(status_code=409, detail="Seat is already occupied.")
        if not runtime.game.can_add_player(
            request.seat_index,
            user_id,
            username,
            request.buy_in_amount,
        ):
            raise HTTPException(status_code=409, detail="Seat or buy-in amount is invalid, or the player is already seated.")

        try:
            buy_in_status = await asyncio.to_thread(
                runtime.repository.reserve_union_buy_in,
                union_id,
                table_id,
                club_id,
                request.seat_index,
                user_id,
                username,
                request.buy_in_amount,
                buy_in_id,
            )
        except (FirestoreRequiredError, GoogleAPICallError) as error:
            raise HTTPException(status_code=503, detail="Union buy-in could not be confirmed.") from error

        if buy_in_status == BuyInStatus.UNION_CLUB_NOT_FOUND:
            raise HTTPException(status_code=403, detail="The club is not an active member of this union.")
        if buy_in_status == BuyInStatus.MEMBER_NOT_FOUND:
            raise HTTPException(status_code=404, detail="Club member account not found.")
        if buy_in_status == BuyInStatus.INSUFFICIENT_BALANCE:
            raise HTTPException(status_code=409, detail="Insufficient club chip balance.")
        if buy_in_status == BuyInStatus.INVALID_BALANCE:
            raise HTTPException(status_code=409, detail="Club member chip balance is invalid.")
        if buy_in_status == BuyInStatus.SEAT_OCCUPIED:
            raise HTTPException(status_code=409, detail="Seat is already reserved.")
        if buy_in_status == BuyInStatus.IDEMPOTENCY_CONFLICT:
            raise HTTPException(status_code=409, detail="buy_in_id was already used for a different reservation.")

        if not runtime.game.add_player(
            request.seat_index,
            user_id,
            username,
            request.buy_in_amount,
            buy_in_id,
            club_id=club_id,
        ):
            raise HTTPException(status_code=500, detail="Buy-in was reserved but union seat could not be assigned.")
        return runtime.game.get_masked_state_for_player(user_id)


@app.post("/clubs/{club_id}/tables/{table_id}/hands", status_code=201)
async def start_hand(
    club_id: str,
    table_id: str,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    runtime = get_table(club_id, table_id)
    if not await runtime.start_hand():
        raise HTTPException(status_code=409, detail="A hand needs at least two seated players and no hand may be running.")
    return runtime.game.get_masked_state_for_player("")


@app.post("/unions/{union_id}/tables/{table_id}/hands", status_code=201)
async def start_union_hand(
    union_id: str,
    table_id: str,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, Any]:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    if not await runtime.start_hand():
        raise HTTPException(status_code=409, detail="A hand needs at least two seated players and no hand may be running.")
    return runtime.game.get_masked_state_for_player("")


@app.websocket("/ws/table/{club_id}/{table_id}")
async def table_websocket_endpoint(
    websocket: WebSocket,
    club_id: str,
    table_id: str,
) -> None:
    runtime = tables.get((club_id, table_id))
    if runtime is None:
        await websocket.close(code=4404, reason="Table not found")
        return
    await authenticate_player_socket(websocket, runtime, club_id)


@app.websocket("/ws/union/{union_id}/{table_id}")
async def union_table_websocket_endpoint(
    websocket: WebSocket,
    union_id: str,
    table_id: str,
) -> None:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        await websocket.close(code=4404, reason="Union table not found")
        return
    await authenticate_player_socket(websocket, runtime, None)


async def authenticate_player_socket(
    websocket: WebSocket,
    runtime: TableRuntime,
    club_id: str | None,
) -> None:
    await websocket.accept()
    registered = False
    try:
        try:
            auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.close(code=4408, reason="Authentication timed out")
            return
        if (
            not isinstance(auth_message, dict)
            or auth_message.get("type") != "authenticate"
            or not isinstance(auth_message.get("id_token"), str)
        ):
            await websocket.close(code=4401, reason="Firebase ID token required")
            return

        if runtime.union_id is not None:
            requested_club_id = auth_message.get("club_id")
            if not isinstance(requested_club_id, str) or not requested_club_id:
                await websocket.close(code=4403, reason="A home club ID is required for union tables")
                return
            club_id = requested_club_id

        if club_id is None:
            await websocket.close(code=4403, reason="Club membership is required")
            return

        try:
            current_user = await verify_id_token(
                auth_message["id_token"],
                websocket.app.state.token_verifier,
            )
            if runtime.union_id is None:
                membership = await load_active_club_member(
                    websocket.app.state.repository,
                    club_id,
                    current_user,
                )
            else:
                membership = await load_active_union_club_member(
                    websocket.app.state.repository,
                    runtime.union_id,
                    club_id,
                    current_user,
                )
        except HTTPException as error:
            close_code = 4401 if error.status_code == 401 else 1013 if error.status_code == 503 else 4403
            await websocket.close(code=close_code, reason=str(error.detail))
            return

        user_id = current_user.uid
        player_seat = next(
            (player for player in runtime.game.seats.values() if player.user_id == user_id),
            None,
        )
        if (
            membership.role != ClubRole.PLAYER
            or player_seat is None
            or (runtime.union_id is not None and player_seat.club_id != club_id)
        ):
            await websocket.close(code=4403, reason="A seated player account is required")
            return

        await connections.connect(
            runtime.connection_scope,
            runtime.game.table_id,
            user_id,
            websocket,
            accepted=True,
        )
        registered = True
        await websocket.send_json({"type": "authenticated", "user_id": user_id})
        await websocket.send_json(runtime.game.get_masked_state_for_player(user_id))
        while True:
            raw_action = await websocket.receive_json()
            try:
                if isinstance(raw_action, dict) and raw_action.get("type") == "pre_action":
                    option = raw_action.get("action")
                    if option is not None and not isinstance(option, str):
                        raise ValueError("Pre-action must be a string or null.")
                    selected = await runtime.set_pre_action(user_id, option)
                    await websocket.send_json(
                        {"type": "pre_action_set", "action": selected}
                    )
                    continue
                request = PlayerAction.model_validate(raw_action)
                await runtime.act(user_id, request.action, request.amount)
            except (ValidationError, ValueError) as error:
                await websocket.send_json({"error": str(error)})
    except WebSocketDisconnect:
        pass
    finally:
        if registered:
            connections.disconnect(
                runtime.connection_scope,
                runtime.game.table_id,
                websocket,
            )