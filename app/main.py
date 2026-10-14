from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import UUID

from google.api_core.exceptions import GoogleAPICallError
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.api.hand_history import router as hand_history_router
from app.auth.roles import (
    AuthenticatedUser,
    ClubMembership,
    ClubRole,
    get_active_club_member,
    get_current_user,
    load_active_club_member,
    require_club_role,
    verify_id_token,
)
from base_poker_game import BasePokerGame
from database import BuyInStatus, FirestoreRequiredError, create_hand_repository
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


class PlayerAction(BaseModel):
    action: str
    amount: int | None = None


app = FastAPI(title="Cowboys PH Poker Game Server")
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
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    tables[key] = TableRuntime(
        club_id, game, connections, repository, turn_timeout_seconds
    )
    return {"club_id": club_id, "table_id": table_id, "status": "created"}


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
        )
        if not seated:
            raise HTTPException(
                status_code=500,
                detail="Buy-in was reserved but the in-memory seat could not be assigned. Retry with the same buy_in_id.",
            )
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

        try:
            current_user = await verify_id_token(
                auth_message["id_token"],
                websocket.app.state.token_verifier,
            )
            membership = await load_active_club_member(
                websocket.app.state.repository,
                club_id,
                current_user,
            )
        except HTTPException as error:
            close_code = 4401 if error.status_code == 401 else 1013 if error.status_code == 503 else 4403
            await websocket.close(code=close_code, reason=str(error.detail))
            return

        user_id = current_user.uid
        if membership.role != ClubRole.PLAYER or user_id not in {
            player.user_id for player in runtime.game.seats.values()
        }:
            await websocket.close(code=4403, reason="A seated player account is required")
            return

        await connections.connect(club_id, table_id, user_id, websocket, accepted=True)
        registered = True
        await websocket.send_json({"type": "authenticated", "user_id": user_id})
        await websocket.send_json(runtime.game.get_masked_state_for_player(user_id))
        while True:
            raw_action = await websocket.receive_json()
            try:
                request = PlayerAction.model_validate(raw_action)
                await runtime.act(user_id, request.action, request.amount)
            except (ValidationError, ValueError) as error:
                await websocket.send_json({"error": str(error)})
    except WebSocketDisconnect:
        pass
    finally:
        if registered:
            connections.disconnect(club_id, table_id, websocket)