from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Any
from uuid import UUID, uuid4

from google.api_core.exceptions import GoogleAPICallError
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ValidationError

from app.api.hand_history import router as hand_history_router
from app.api.wallets import router as wallet_router
from app.errors import (
    ERROR_DEFINITIONS,
    ErrorCode,
    HTTP_STATUS_CODES,
    error_body,
    install_error_handlers,
    log_error,
    new_trace_id,
)
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
from database import (
    BuyInStatus,
    FirestoreHandRepository,
    FirestoreRequiredError,
    TableActionCommitStatus,
    TableLeaseConflictError,
    TableLeaseExpiredError,
    UnionRequestStatus,
    UnionTableCloseStatus,
    create_hand_repository,
    table_action_fingerprint,
)
from logic.auth import FirebaseTokenVerifier
from logic.connections import ConnectionManager
from logic.first_party_table import FirstPartyNLHGame
from logic.game_catalog import (
    SUPPORTED_GAME_ID,
    get_game_catalog,
    is_game_create_supported,
)
from logic.runtime import ActionIdempotencyConflict, TableRuntime, TableStateConflict
from logic.table_checkpoint import deserialize_first_party_table, serialize_first_party_table
from logic.pubsub_transport import PubSubTransport
from logic.redis_presence import RedisSocketPresence, redis_presence_from_url
from logic.table_game_base import TableGameBase
from logic.table_ownership import (
    LeaseAlreadyHeldError,
    LeaseExpiredError,
    TableLeaseManager,
    WorkerCoordinator,
    WorkerCoordinatorRegistry,
)
from logic.websocket_protocol import (
    MalformedWebSocketMessage,
    PROTOCOL_VERSION,
    UnsupportedWebSocketVersion,
    game_topic,
    make_envelope,
    parse_client_envelope,
)


class CreateTableRequest(BaseModel):
    game_id: str = Field(default=SUPPORTED_GAME_ID, min_length=1)
    small_blind: int = Field(gt=0)
    big_blind: int = Field(gt=0)
    buy_in_min: int = Field(gt=0)
    buy_in_max: int = Field(gt=0)
    table_size: int | None = Field(default=None, ge=2)
    union_id: str | None = Field(default=None, min_length=1, max_length=128)
    table_name: str | None = Field(default=None, min_length=1, max_length=100)
    theme_id: str | None = Field(default=None, min_length=1, max_length=40)


class SeatPlayerRequest(BaseModel):
    seat_index: int = Field(ge=0)
    buy_in_amount: int = Field(gt=0, strict=True)
    buy_in_id: UUID
    accept_big_blind_post: bool = False


class StartHandRequest(BaseModel):
    straddle_player_id: str | None = Field(default=None, min_length=1, max_length=128)


class TableLeaseClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    force_reclaim: bool = False
    scope_type: str | None = Field(default=None, pattern="^(club|union)$")
    scope_id: str | None = Field(default=None, min_length=1, max_length=128)


class UpdateTableSettingsRequest(BaseModel):
    settings_update_id: UUID
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


class CreateUnionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    union_code: str = Field(pattern=r"^[A-Z]$")
    theme_id: str = Field(default="standard", min_length=1, max_length=40)


class PlayerAction(BaseModel):
    action: str
    amount: int | None = None


class GameCatalogEntry(BaseModel):
    game_id: str
    display_name: str
    category: str
    backend_status: str
    create_supported: bool


class GameCatalogResponse(BaseModel):
    active_game_id: str
    games: list[GameCatalogEntry]


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(repository.recover_pending_settlements)
    global pubsub_transport, redis_presence, worker_maintenance_task
    if os.getenv("PUBSUB_ROUTING_ENABLED", "false").lower() == "true":
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("FIREBASE_PROJECT_ID")
        prefix = os.getenv("PUBSUB_RESOURCE_PREFIX")
        redis_url = os.getenv("REDIS_URL")
        if not project_id or not prefix or not redis_url:
            raise RuntimeError(
                "Pub/Sub routing requires GOOGLE_CLOUD_PROJECT, PUBSUB_RESOURCE_PREFIX, and REDIS_URL."
            )
        service_role = os.getenv("SERVICE_ROLE", "gateway")
        if service_role == "gateway":
            gateway_id = os.getenv("GATEWAY_ID") or str(uuid4())
            pubsub_transport = PubSubTransport(
                project_id,
                prefix=prefix,
                gateway_id=gateway_id,
            )
            redis_presence = redis_presence_from_url(
                redis_url,
                gateway_id,
                int(os.getenv("SOCKET_PRESENCE_TTL_SECONDS", "120")),
            )
            await pubsub_transport.start_gateway(route_gateway_event)
        elif service_role == "worker":
            worker_id = os.getenv("WORKER_ID")
            if not worker_id:
                raise RuntimeError("Worker service requires a stable WORKER_ID.")
            session = await asyncio.to_thread(
                repository.start_worker_session,
                worker_id,
                worker_session_ttl_seconds,
            )
            if session.get("already_active"):
                raise RuntimeError("This WORKER_ID already has an active runtime instance.")
            pubsub_transport = PubSubTransport(
                project_id,
                prefix=prefix,
                worker_id=worker_id,
            )
            redis_presence = redis_presence_from_url(
                redis_url,
                f"worker:{worker_id}",
                int(os.getenv("SOCKET_PRESENCE_TTL_SECONDS", "120")),
            )
            await pubsub_transport.start_worker(handle_worker_command)
            worker_maintenance_task = asyncio.create_task(
                maintain_worker_leases(worker_id),
                name=f"worker-lease-maintenance:{worker_id}",
            )
        else:
            raise RuntimeError("SERVICE_ROLE must be gateway or worker when Pub/Sub routing is enabled.")
    try:
        yield
    finally:
        if worker_maintenance_task is not None:
            worker_maintenance_task.cancel()
            try:
                await worker_maintenance_task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                error_logger.exception("Worker maintenance stopped unexpectedly: %s", error)
            worker_maintenance_task = None
        if pubsub_transport is not None:
            try:
                await pubsub_transport.close()
            except Exception as error:
                error_logger.exception("Pub/Sub shutdown failed: %s", error)
            pubsub_transport = None
        if (
            os.getenv("SERVICE_ROLE") == "worker"
            and os.getenv("WORKER_ID")
            and isinstance(repository, FirestoreHandRepository)
        ):
            worker_id = os.environ["WORKER_ID"]
            try:
                leases = await asyncio.to_thread(
                    repository.list_worker_table_leases,
                    worker_id,
                )
                for record in leases:
                    await asyncio.to_thread(
                        repository.release_table_lease,
                        str(record["scope_type"]),
                        str(record["scope_id"]),
                        str(record["table_id"]),
                        worker_id,
                        int(record["fencing_generation"]),
                    )
                await asyncio.to_thread(repository.stop_worker_session, worker_id)
            except (FirestoreRequiredError, GoogleAPICallError, KeyError, ValueError) as error:
                error_logger.exception("Worker shutdown cleanup failed: %s", error)
        if redis_presence is not None:
            try:
                await redis_presence.close()
            except Exception as error:
                error_logger.exception("Redis presence shutdown failed: %s", error)
            redis_presence = None


app = FastAPI(title="Cowboys PH Poker Game Server", lifespan=lifespan)
error_logger = logging.getLogger("cowboys.errors")
install_error_handlers(app)


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
app.include_router(wallet_router)
connections = ConnectionManager()
repository = create_hand_repository(os.getenv("FIRESTORE_ENABLED", "false").lower() == "true")
app.state.repository = repository
app.state.token_verifier = FirebaseTokenVerifier(
    os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
)
tables: dict[tuple[str, str], TableRuntime] = {}
shared_table_lease_manager = TableLeaseManager(
    ttl_seconds=int(os.getenv("TABLE_LEASE_TTL_SECONDS", "30"))
)
worker_session_ttl_seconds = max(shared_table_lease_manager.ttl_seconds * 3, 60)
worker_coordinators = WorkerCoordinatorRegistry(shared_table_lease_manager)
runtime_restore_lock = asyncio.Lock()
pubsub_transport: PubSubTransport | None = None
redis_presence: RedisSocketPresence | None = None
worker_maintenance_task: asyncio.Task[None] | None = None
max_seats = int(os.getenv("MAX_SEATS", "9"))
turn_timeout_seconds = int(os.getenv("TURN_TIMEOUT_SECONDS", "30"))
websocket_heartbeat_interval_seconds = int(
    os.getenv("WEBSOCKET_HEARTBEAT_INTERVAL_SECONDS", "30")
)
websocket_idle_timeout_seconds = int(os.getenv("WEBSOCKET_IDLE_TIMEOUT_SECONDS", "90"))
worker_bearer_scheme = HTTPBearer(auto_error=False)


def requested_table_size(table_size: int | None) -> int:
    seat_capacity = max_seats if table_size is None else table_size
    if seat_capacity > max_seats:
        raise HTTPException(
            status_code=422,
            detail="Requested table size exceeds the server maximum.",
        )
    return seat_capacity


def requested_game_id(game_id: str) -> str:
    if not is_game_create_supported(game_id):
        raise HTTPException(
            status_code=422,
            detail="Requested game is not supported for table creation.",
        )
    return game_id


def get_table(club_id: str, table_id: str) -> TableRuntime:
    runtime = tables.get((club_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Table not found.")
    return runtime


async def reserve_seat_buy_in(
    runtime: TableRuntime,
    reserve_method: Any,
    reserve_arguments: tuple[Any, ...],
    seat_index: int,
    user_id: str,
    username: str,
    buy_in_amount: int,
    buy_in_id: str,
    club_id: str,
    accept_big_blind_post: bool,
) -> tuple[BuyInStatus, bool]:
    if not isinstance(runtime.repository, FirestoreHandRepository):
        return await asyncio.to_thread(reserve_method, *reserve_arguments), False

    lease = runtime.current_lease()
    if lease is None or runtime._worker_id != lease.owner_worker_id:
        raise HTTPException(status_code=409, detail="This runtime does not own the table lease.")
    if not isinstance(runtime.game, FirstPartyNLHGame):
        raise HTTPException(status_code=409, detail="Only first-party tables support durable seating.")

    before_checkpoint = serialize_first_party_table(runtime.game)
    if not runtime.game.add_player(
        seat_index,
        user_id,
        username,
        buy_in_amount,
        buy_in_id,
        club_id=club_id,
        accept_big_blind_post=accept_big_blind_post,
    ):
        return BuyInStatus.SEAT_OCCUPIED, False

    try:
        status = await asyncio.to_thread(
            reserve_method,
            *reserve_arguments,
            runtime_checkpoint=serialize_first_party_table(runtime.game),
            owner_id=lease.owner_worker_id,
            fencing_epoch=lease.fencing_generation,
            expected_sequence=runtime._table_sequence,
        )
    except Exception:
        runtime.game = deserialize_first_party_table(before_checkpoint)
        raise

    if status is BuyInStatus.RESERVED:
        runtime._table_sequence += 1
        return status, True

    runtime.game = deserialize_first_party_table(before_checkpoint)
    if status is BuyInStatus.TABLE_STATE_CONFLICT:
        await runtime._restore_persisted_checkpoint()
    return status, False


async def update_runtime_table_settings(
    runtime: TableRuntime,
    user_id: str,
    request: UpdateTableSettingsRequest,
) -> dict[str, Any]:
    async with runtime.lock:
        durable_repository = (
            runtime.repository
            if isinstance(runtime.repository, FirestoreHandRepository)
            else None
        )
        before_checkpoint = None
        lease = None
        if durable_repository is not None:
            if not isinstance(runtime.game, FirstPartyNLHGame):
                raise HTTPException(status_code=409, detail="This table cannot be checkpointed.")
            lease = runtime.current_lease()
            if lease is None or runtime._worker_id != lease.owner_worker_id:
                raise HTTPException(status_code=409, detail="This runtime does not own the table lease.")
            before_checkpoint = serialize_first_party_table(runtime.game)

        settings = request.model_dump(
            mode="json",
            exclude={"settings_update_id"},
            exclude_none=True,
        )
        try:
            payload = runtime.game.update_table_settings(**settings)
        except ValueError as error:
            if before_checkpoint is not None:
                runtime.game = deserialize_first_party_table(before_checkpoint)
            raise HTTPException(status_code=422, detail="Table settings are invalid.") from error

        if durable_repository is None:
            return payload

        assert lease is not None
        action_id = str(request.settings_update_id)
        payload_hash = table_action_fingerprint(
            user_id,
            action_id,
            {
                "event": "UPDATE_TABLE_SETTINGS",
                "table_id": runtime.game.table_id,
                "settings": settings,
            },
        )
        try:
            result = await asyncio.to_thread(
                durable_repository.commit_table_action,
                *runtime._lease_scope(),
                runtime.game.table_id,
                lease.owner_worker_id,
                lease.fencing_generation,
                runtime._table_sequence,
                user_id,
                action_id,
                payload_hash,
                serialize_first_party_table(runtime.game),
            )
        except Exception:
            assert before_checkpoint is not None
            runtime.game = deserialize_first_party_table(before_checkpoint)
            raise

        if result.status is TableActionCommitStatus.COMMITTED:
            runtime._table_sequence = result.sequence
            return payload
        if result.status is TableActionCommitStatus.IDEMPOTENT_REPLAY:
            await runtime._restore_persisted_checkpoint()
            return runtime.game.table_settings.as_dict()

        await runtime._restore_persisted_checkpoint()
        if result.status is TableActionCommitStatus.IDEMPOTENCY_CONFLICT:
            raise HTTPException(
                status_code=409,
                detail="settings_update_id was already used with different settings.",
            )
        raise HTTPException(
            status_code=409,
            detail="The table lease or state changed before settings were committed.",
        )


def union_table_key(union_id: str, table_id: str) -> tuple[str, str]:
    return (f"union:{union_id}", table_id)


def table_lobby_entry(game: TableGameBase) -> dict[str, Any]:
    return {
        "table_id": game.table_id,
        "game_id": game.game_id,
        "table_name": game.table_name,
        "is_union_table": game.union_id is not None,
        "union_id": game.union_id,
        "union_code": game.union_code,
        "origin_club_id": game.origin_club_id,
        "theme_id": game.theme_id,
        "small_blind": game.small_blind,
        "big_blind": game.big_blind,
        "buy_in_min": game.buy_in_min,
        "buy_in_max": game.buy_in_max,
        "max_seats": game.max_seats,
        "seated_count": len(game.seats),
        "is_hand_running": game.is_hand_running,
        **game.table_settings.as_dict(),
    }


async def require_union_table_origin_operator(
    runtime: TableRuntime,
    current_user: AuthenticatedUser,
) -> ClubMembership:
    origin_club_id = runtime.game.origin_club_id
    if runtime.union_id is None or origin_club_id is None:
        raise HTTPException(status_code=409, detail="Union table has no valid origin club.")
    membership = await load_active_club_member(repository, origin_club_id, current_user)
    if membership.role not in {ClubRole.OWNER, ClubRole.MANAGER}:
        raise HTTPException(status_code=403, detail="The origin club owner or manager is required.")
    try:
        is_active_union_member = await asyncio.to_thread(
            repository.is_club_in_union,
            runtime.union_id,
            origin_club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union membership could not be verified.") from error
    if not is_active_union_member:
        raise HTTPException(status_code=403, detail="The origin club is no longer an active union member.")
    return membership


def table_lease_payload(runtime: TableRuntime) -> dict[str, Any]:
    lease = runtime.current_lease()
    if lease is None:
        return {
            "table_id": runtime.game.table_id,
            "owner_worker_id": None,
            "fencing_generation": None,
            "lease_expires_at": None,
            "status": "unowned",
        }
    return {
        "table_id": runtime.game.table_id,
        "owner_worker_id": lease.owner_worker_id,
        "fencing_generation": lease.fencing_generation,
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "status": lease.status,
    }


def get_worker_coordinator(worker_id: str) -> WorkerCoordinator:
    return worker_coordinators.get_or_create(worker_id)


def require_worker_session_started(worker_id: str, *, allow_unstarted: bool = False) -> WorkerCoordinator:
    coordinator = get_worker_coordinator(worker_id)
    if not coordinator.session._active and not allow_unstarted:
        raise HTTPException(
            status_code=409,
            detail="Worker session must be started before claiming or renewing table leases.",
        )
    return coordinator


async def get_or_restore_scoped_runtime(
    scope_type: str,
    scope_id: str,
    table_id: str,
) -> TableRuntime:
    if scope_type not in {"club", "union"} or not scope_id or not table_id:
        raise HTTPException(status_code=422, detail="A valid table scope is required.")
    key = (scope_id, table_id) if scope_type == "club" else union_table_key(scope_id, table_id)
    async with runtime_restore_lock:
        runtime = tables.get(key)
        if runtime is not None:
            if (
                isinstance(repository, FirestoreHandRepository)
                and os.getenv("SERVICE_ROLE", "gateway") == "gateway"
            ):
                try:
                    await runtime.load_persisted_state()
                except (FirestoreRequiredError, GoogleAPICallError, ValueError) as error:
                    raise HTTPException(
                        status_code=503,
                        detail="Table checkpoint recovery failed.",
                    ) from error
            if os.getenv("SERVICE_ROLE", "gateway") == "worker":
                runtime.on_state_change = publish_masked_table_snapshots
            return runtime
        if not isinstance(repository, FirestoreHandRepository):
            raise HTTPException(status_code=404, detail="Table not found.")
        try:
            payload = await asyncio.to_thread(
                repository.get_table_checkpoint,
                scope_type,
                scope_id,
                table_id,
            )
        except (FirestoreRequiredError, GoogleAPICallError) as error:
            raise HTTPException(status_code=503, detail="Table checkpoint is unavailable.") from error
        checkpoint = payload.get("checkpoint") if payload is not None else None
        if checkpoint is None:
            raise HTTPException(status_code=404, detail="Table checkpoint not found.")
        try:
            game = deserialize_first_party_table(checkpoint)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=409, detail="Table checkpoint cannot be recovered.") from error
        runtime = TableRuntime(
            game.origin_club_id or (scope_id if scope_type == "club" else ""),
            game,
            connections,
            repository,
            turn_timeout_seconds,
            union_id=scope_id if scope_type == "union" else None,
            lease_manager=shared_table_lease_manager,
            on_state_change=(
                publish_masked_table_snapshots
                if os.getenv("SERVICE_ROLE", "gateway") == "worker"
                else None
            ),
        )
        try:
            if not await runtime.load_persisted_state():
                raise HTTPException(status_code=409, detail="Table checkpoint could not be restored.")
        except (FirestoreRequiredError, GoogleAPICallError, ValueError) as error:
            raise HTTPException(status_code=503, detail="Table checkpoint recovery failed.") from error
        tables[key] = runtime
        return runtime


async def route_gateway_event(event: dict[str, Any]) -> None:
    scope = event.get("scope")
    table_id = event.get("table_id")
    user_id = event.get("user_id")
    state = event.get("state")
    if (
        isinstance(scope, str)
        and isinstance(table_id, str)
        and isinstance(user_id, str)
        and isinstance(state, dict)
    ):
        await connections.send_user_state(scope, table_id, user_id, state)


async def publish_masked_table_snapshots(runtime: TableRuntime) -> None:
    if pubsub_transport is None or redis_presence is None:
        raise RuntimeError("Worker snapshot routing is not configured.")
    for seat in runtime.game.seats.values():
        gateways = await redis_presence.gateways_for_user(
            runtime.connection_scope,
            runtime.game.table_id,
            seat.user_id,
        )
        for gateway_id in gateways:
            pubsub_transport.publish_gateway_event(
                gateway_id=gateway_id,
                event={
                    "scope": runtime.connection_scope,
                    "table_id": runtime.game.table_id,
                    "user_id": seat.user_id,
                    "state": {
                        **runtime.game.get_masked_state_for_player(seat.user_id),
                        "table_sequence": runtime._table_sequence,
                    },
                },
                ordering_key=(
                    f"{runtime.connection_scope}:{runtime.game.table_id}:{seat.user_id}"
                ),
            )


async def maintain_worker_leases(worker_id: str) -> None:
    interval = max(1, shared_table_lease_manager.ttl_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        if not isinstance(repository, FirestoreHandRepository):
            return
        active = await asyncio.to_thread(
            repository.heartbeat_worker_session,
            worker_id,
            worker_session_ttl_seconds,
        )
        if not active:
            error_logger.critical("Durable worker session expired for worker %s.", worker_id)
            return
        leases = await asyncio.to_thread(repository.list_worker_table_leases, worker_id)
        for record in leases:
            scope_type = record.get("scope_type")
            scope_id = record.get("scope_id")
            table_id = record.get("table_id")
            generation = record.get("fencing_generation")
            if (
                scope_type not in {"club", "union"}
                or not isinstance(scope_id, str)
                or not isinstance(table_id, str)
                or not isinstance(generation, int)
            ):
                continue
            try:
                runtime = await get_or_restore_scoped_runtime(scope_type, scope_id, table_id)
                await asyncio.to_thread(runtime.renew_lease, worker_id, generation)
            except (HTTPException, LeaseAlreadyHeldError, LeaseExpiredError, GoogleAPICallError) as error:
                error_logger.warning(
                    "Could not renew worker lease for %s/%s/%s: %s",
                    scope_type,
                    scope_id,
                    table_id,
                    error,
                )


async def handle_worker_command(envelope: dict[str, Any]) -> dict[str, Any]:
    worker_id = os.getenv("WORKER_ID")
    if worker_id is None or envelope.get("worker_id") != worker_id:
        return {"ok": False, "error": "Worker identity mismatch."}
    if not isinstance(repository, FirestoreHandRepository) or not await asyncio.to_thread(
        repository.worker_session_is_active,
        worker_id,
    ):
        return {"ok": False, "error": "Worker session is not active."}
    scope_type = envelope["scope_type"]
    scope_id = envelope["scope_id"]
    table_id = envelope["table_id"]
    command = envelope["command"]
    try:
        runtime = await get_or_restore_scoped_runtime(scope_type, scope_id, table_id)
        lease = runtime.current_lease()
        if lease is None or lease.owner_worker_id != worker_id:
            return {"ok": False, "error": "Worker no longer owns this table."}
        event = command.get("event")
        user_id = command.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            return {"ok": False, "error": "Authenticated player identity is required."}
        if event == "PLAYER_ACTION":
            replayed = await runtime.act(
                user_id,
                command["action"],
                command.get("amount"),
                command["action_id"],
            )
            if replayed:
                await publish_masked_table_snapshots(runtime)
            return {"ok": True, "replayed": replayed}
        if event == "PRE_ACTION":
            selected = await runtime.set_pre_action(
                user_id,
                command.get("option"),
                envelope["request_id"],
            )
            await publish_masked_table_snapshots(runtime)
            return {"ok": True, "action": selected}
        return {"ok": False, "error": "Unsupported worker command."}
    except ActionIdempotencyConflict:
        return {"ok": False, "conflict": True, "error": "Action identifier conflict."}
    except (
        ValueError,
        TableStateConflict,
        LeaseExpiredError,
        LeaseAlreadyHeldError,
        TableLeaseExpiredError,
        TableLeaseConflictError,
    ):
        return {"ok": False, "conflict": True, "error": "Table action was rejected."}


async def request_owner_command(
    runtime: TableRuntime,
    user_id: str,
    command: dict[str, Any],
) -> dict[str, Any]:
    if pubsub_transport is None or pubsub_transport.gateway_id is None:
        raise RuntimeError("Gateway command routing is not configured.")
    if not isinstance(repository, FirestoreHandRepository):
        raise RuntimeError("Durable table ownership is required for owner routing.")
    scope_type, scope_id = runtime._lease_scope()
    lease = await asyncio.to_thread(
        repository.get_table_lease,
        scope_type,
        scope_id,
        runtime.game.table_id,
    )
    if lease is None or lease.get("owner_id") is None:
        raise RuntimeError("The table does not have an active owner.")
    return await pubsub_transport.request_worker(
        worker_id=str(lease["owner_id"]),
        scope_type=scope_type,
        scope_id=scope_id,
        table_id=runtime.game.table_id,
        command={**command, "user_id": user_id},
    )


async def require_worker_coordination_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(worker_bearer_scheme),
    coordination_token: str | None = Header(default=None, alias="X-Worker-Coordination-Token"),
) -> None:
    if os.getenv("SERVICE_ROLE", "gateway") != "worker":
        raise HTTPException(status_code=404, detail="Not found.")
    configured_token = os.getenv("WORKER_COORDINATION_TOKEN")
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="Worker coordination is not configured.",
        )
    supplied_token = coordination_token
    if supplied_token is None and credentials is not None and credentials.scheme.lower() == "bearer":
        supplied_token = credentials.credentials
    if (
        supplied_token is None
        or not compare_digest(supplied_token, configured_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Worker authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/games", response_model=GameCatalogResponse)
async def list_games() -> GameCatalogResponse:
    return GameCatalogResponse(
        active_game_id=SUPPORTED_GAME_ID,
        games=get_game_catalog(),
    )


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


@app.get("/unions/code/{union_code}")
async def get_union_by_code(
    union_code: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        union = await asyncio.to_thread(repository.get_union_by_code, union_code.upper())
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union lookup is unavailable.") from error
    if union is None or union.get("status") != "active":
        raise HTTPException(status_code=404, detail="Union not found.")
    return {
        "union_id": str(union["union_id"]),
        "union_code": str(union.get("union_code") or union_code.upper()),
        "name": str(union.get("name") or ""),
        "theme_id": str(union.get("theme_id") or "standard"),
        "status": "active",
    }


@app.post("/clubs/{club_id}/unions", status_code=201)
async def create_union(
    club_id: str,
    request: CreateUnionRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, str]:
    union_id = str(uuid4())
    union_data = {
        "union_id": union_id,
        "name": request.name,
        "union_code": request.union_code,
        "theme_id": request.theme_id,
        "status": "active",
        "created_by": membership.user.uid,
    }
    try:
        created = await asyncio.to_thread(
            repository.create_union,
            union_id,
            union_data,
            request.union_code,
            membership.user.uid,
            club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union could not be created.") from error
    if not created:
        raise HTTPException(status_code=409, detail="Union code is already in use.")
    return {
        "union_id": union_id,
        "union_code": request.union_code,
        "name": request.name,
        "theme_id": request.theme_id,
        "founding_club_id": club_id,
        "status": "active",
    }


@app.post("/clubs/{club_id}/unions/{union_id}/representative-requests", status_code=202)
async def request_union_representative(
    club_id: str,
    union_id: str,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, str]:
    try:
        status = await asyncio.to_thread(
            repository.request_union_representative,
            union_id,
            membership.user.uid,
            club_id,
            membership.role.value,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union representative request failed.") from error
    if status is UnionRequestStatus.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Union not found or inactive.")
    return {"union_id": union_id, "user_id": membership.user.uid, "status": status.value}


@app.get("/unions/{union_id}/representative-requests")
async def list_union_representative_requests(
    union_id: str,
    membership: UnionMembership = Depends(require_union_admin()),
) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(repository.list_union_representative_requests, union_id)
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union requests are unavailable.") from error


@app.post("/unions/{union_id}/representatives/{user_id}/approve")
async def approve_union_representative(
    union_id: str,
    user_id: str,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, str]:
    try:
        status = await asyncio.to_thread(
            repository.approve_union_representative,
            union_id,
            user_id,
            membership.user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union representative could not be approved.") from error
    if status is UnionRequestStatus.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Pending representative request not found.")
    if status is UnionRequestStatus.NOT_AUTHORIZED:
        raise HTTPException(status_code=403, detail="Representative is no longer an active club owner or manager.")
    return {"union_id": union_id, "user_id": user_id, "status": status.value}


@app.post("/clubs/{club_id}/unions/{union_id}/requests", status_code=202)
async def request_union_club_membership(
    club_id: str,
    union_id: str,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, str]:
    try:
        union_profile = await asyncio.to_thread(repository.get_union_profile, union_id)
        if union_profile is None or union_profile.get("status") != "active":
            raise HTTPException(status_code=404, detail="Union not found or inactive.")
        status = await asyncio.to_thread(
            repository.request_union_club,
            union_id,
            club_id,
            str(union_profile.get("name") or union_id),
            str(union_profile.get("union_code") or ""),
            membership.user.uid,
        )
    except HTTPException:
        raise
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union club request failed.") from error
    if status is UnionRequestStatus.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Union not found or inactive.")
    if status is UnionRequestStatus.NOT_AUTHORIZED:
        raise HTTPException(status_code=403, detail="An approved union representative for this club is required.")
    return {"union_id": union_id, "club_id": club_id, "status": status.value}


@app.get("/unions/{union_id}/club-requests")
async def list_union_club_requests(
    union_id: str,
    membership: UnionMembership = Depends(require_union_admin()),
) -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(repository.list_union_club_requests, union_id)
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union club requests are unavailable.") from error


@app.post("/unions/{union_id}/clubs/{club_id}/approve")
async def approve_union_club_membership(
    union_id: str,
    club_id: str,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, str]:
    try:
        status = await asyncio.to_thread(
            repository.approve_union_club,
            union_id,
            club_id,
            membership.user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union club could not be approved.") from error
    if status is UnionRequestStatus.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Pending union club request not found.")
    if status is UnionRequestStatus.NOT_AUTHORIZED:
        raise HTTPException(status_code=403, detail="The requesting representative is no longer authorized for this club.")
    return {"union_id": union_id, "club_id": club_id, "status": status.value}


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
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> None:
    raise HTTPException(
        status_code=409,
        detail="Clubs must request union membership and wait for approval.",
    )


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
    payload = await update_runtime_table_settings(runtime, membership.user.uid, request)
    return {
        "table_id": table_id,
        "table_name": runtime.game.table_name,
        "settings_update_id": str(request.settings_update_id),
        **payload,
    }


@app.patch("/unions/{union_id}/tables/{table_id}/settings")
async def update_union_table_settings(
    union_id: str,
    table_id: str,
    request: UpdateTableSettingsRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    membership = await require_union_table_origin_operator(runtime, current_user)
    payload = await update_runtime_table_settings(runtime, membership.user.uid, request)
    return {
        "union_id": union_id,
        "table_id": table_id,
        "table_name": runtime.game.table_name,
        "settings_update_id": str(request.settings_update_id),
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
) -> dict[str, Any]:
    union_id = request.union_id
    key = union_table_key(union_id, table_id) if union_id else (club_id, table_id)
    if key in tables:
        raise HTTPException(status_code=409, detail="Table already exists.")
    game_id = requested_game_id(request.game_id)
    seat_capacity = requested_table_size(request.table_size)
    union_profile = None
    if union_id is not None:
        try:
            union_profile = await asyncio.to_thread(repository.get_union_profile, union_id)
            club_is_member = await asyncio.to_thread(
                repository.is_club_in_union,
                union_id,
                club_id,
            )
        except (FirestoreRequiredError, GoogleAPICallError) as error:
            raise HTTPException(status_code=503, detail="Union membership could not be verified.") from error
        if union_profile is None or union_profile.get("status") != "active":
            raise HTTPException(status_code=404, detail="Union not found.")
        if not club_is_member:
            raise HTTPException(status_code=403, detail="The club is not an active member of this union.")
    table_name = request.table_name or (
        f"Table {table_id} - Union {union_code}"
        if union_id
        else table_id
    )
    theme_id = (
        str(union_profile.get("theme_id") or "standard")
        if union_profile is not None
        else request.theme_id or "standard"
    )
    union_code = (union_profile or {}).get("union_code")
    try:
        game = FirstPartyNLHGame(
            table_id,
            request.small_blind,
            request.big_blind,
            request.buy_in_min,
            request.buy_in_max,
            seat_capacity,
            union_id=union_id,
            origin_club_id=club_id,
            table_name=table_name,
            game_id=game_id,
            theme_id=theme_id,
            union_code=union_code,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        if union_id is not None:
            table_data = {
                "union_id": union_id,
                "table_id": table_id,
                "game_id": game.game_id,
                "table_name": table_name,
                "origin_club_id": club_id,
                "union_code": union_code,
                "theme_id": theme_id,
                "small_blind": game.small_blind,
                "big_blind": game.big_blind,
                "buy_in_min": game.buy_in_min,
                "buy_in_max": game.buy_in_max,
                "max_seats": game.max_seats,
                "status": "open",
            }
            if isinstance(repository, FirestoreHandRepository):
                table_created = await asyncio.to_thread(
                    repository.create_union_table_record,
                    union_id,
                    table_id,
                    table_data,
                    runtime_checkpoint=serialize_first_party_table(game),
                    required_active_club_id=club_id,
                )
            else:
                table_created = await asyncio.to_thread(
                    repository.create_union_table_record,
                    union_id,
                    table_id,
                    table_data,
                )
            if not table_created:
                raise HTTPException(status_code=409, detail="Table already exists.")
        elif isinstance(repository, FirestoreHandRepository):
            checkpoint_initialized = await asyncio.to_thread(
                repository.initialize_table_checkpoint,
                "club",
                club_id,
                table_id,
                None,
                0,
                serialize_first_party_table(game),
            )
            if not checkpoint_initialized:
                raise HTTPException(status_code=409, detail="Table already exists.")
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Table could not be persisted.") from error
    tables[key] = TableRuntime(
        club_id,
        game,
        connections,
        repository,
        turn_timeout_seconds,
        union_id=union_id,
        lease_manager=shared_table_lease_manager,
    )
    return {
        "club_id": club_id,
        "table_id": table_id,
        "game_id": game.game_id,
        "union_id": union_id,
        "table_name": game.table_name,
        "theme_id": game.theme_id,
        "status": "created",
    }


@app.post("/unions/{union_id}/tables/{table_id}")
async def create_union_table(
    union_id: str,
    table_id: str,
    membership: AuthenticatedUser = Depends(get_current_user),
) -> None:
    raise HTTPException(
        status_code=409,
        detail="Create the table through its club route and include union_id to share it.",
    )


@app.post("/workers/{worker_id}/start")
async def start_worker_coordinator(
    worker_id: str,
    _: None = Depends(require_worker_coordination_token),
) -> dict[str, Any]:
    if isinstance(repository, FirestoreHandRepository):
        session = await asyncio.to_thread(
            repository.start_worker_session,
            worker_id,
            worker_session_ttl_seconds,
        )
        if session.get("already_active"):
            if not (
                os.getenv("SERVICE_ROLE") == "worker"
                and os.getenv("WORKER_ID") == worker_id
            ):
                raise HTTPException(status_code=409, detail="Worker session is already active.")
        assigned_tables = await asyncio.to_thread(
            repository.list_worker_table_leases,
            worker_id,
        )
    else:
        coordinator = get_worker_coordinator(worker_id)
        coordinator.start()
        session = {"status": "started"}
        assigned_tables = coordinator.assigned_tables()
    return {
        "worker_id": worker_id,
        "status": session["status"],
        "assigned_tables": assigned_tables,
    }


@app.get("/workers/{worker_id}/tables")
async def list_worker_tables(
    worker_id: str,
    _: None = Depends(require_worker_coordination_token),
) -> dict[str, Any]:
    if isinstance(repository, FirestoreHandRepository):
        assigned_tables = await asyncio.to_thread(
            repository.list_worker_table_leases,
            worker_id,
        )
    else:
        coordinator = get_worker_coordinator(worker_id)
        assigned_tables = coordinator.assigned_tables()
    return {
        "worker_id": worker_id,
        "assigned_tables": assigned_tables,
    }


@app.post("/workers/{worker_id}/tables/{table_id}/lease")
async def claim_worker_table_lease(
    worker_id: str,
    table_id: str,
    request: TableLeaseClaimRequest | None = None,
    _: None = Depends(require_worker_coordination_token),
) -> dict[str, Any]:
    claim = request or TableLeaseClaimRequest(worker_id=worker_id)
    if claim.worker_id != worker_id:
        raise HTTPException(
            status_code=400,
            detail="Worker ID in path and payload must match.",
        )
    if claim.force_reclaim:
        raise HTTPException(
            status_code=403,
            detail="Forced lease reclamation is disabled for worker API requests.",
        )
    if isinstance(repository, FirestoreHandRepository):
        if claim.scope_type is None or claim.scope_id is None:
            raise HTTPException(
                status_code=422,
                detail="Scope type and scope ID are required for durable table claims.",
            )
        session_active = await asyncio.to_thread(
            repository.worker_session_is_active,
            worker_id,
        )
        if not session_active:
            raise HTTPException(
                status_code=409,
                detail="Worker session must be started before claiming or renewing table leases.",
            )
        runtime = await get_or_restore_scoped_runtime(
            claim.scope_type,
            claim.scope_id,
            table_id,
        )
        try:
            lease = runtime.claim_lease(worker_id, force_reclaim=claim.force_reclaim)
        except LeaseAlreadyHeldError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except LeaseExpiredError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    else:
        coordinator = require_worker_session_started(worker_id)
        lease = coordinator.bind_table(table_id, force_reclaim=claim.force_reclaim)
    return {
        "worker_id": worker_id,
        "table_id": table_id,
        "owner_worker_id": lease.owner_worker_id,
        "fencing_generation": lease.fencing_generation,
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "status": lease.status,
    }


@app.post("/workers/{worker_id}/tables/{table_id}/heartbeat")
async def renew_worker_table_lease(
    worker_id: str,
    table_id: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    _: None = Depends(require_worker_coordination_token),
) -> dict[str, Any]:
    if isinstance(repository, FirestoreHandRepository):
        if scope_type not in {"club", "union"} or not scope_id:
            raise HTTPException(status_code=422, detail="Scope type and scope ID are required.")
        active = await asyncio.to_thread(
            repository.heartbeat_worker_session,
            worker_id,
            worker_session_ttl_seconds,
        )
        if not active:
            raise HTTPException(status_code=409, detail="Worker session is not active.")
        runtime = await get_or_restore_scoped_runtime(scope_type, scope_id, table_id)
        current = runtime.current_lease()
        if current is None or current.owner_worker_id != worker_id:
            raise HTTPException(status_code=404, detail="Worker does not own the requested table lease.")
        try:
            lease = runtime.renew_lease(worker_id, current.fencing_generation)
        except (LeaseAlreadyHeldError, LeaseExpiredError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    else:
        coordinator = require_worker_session_started(worker_id)
        lease = coordinator.heartbeat(table_id)
    return {
        "worker_id": worker_id,
        "table_id": table_id,
        "owner_worker_id": lease.owner_worker_id,
        "fencing_generation": lease.fencing_generation,
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "status": lease.status,
    }


@app.delete("/workers/{worker_id}/tables/{table_id}/lease", status_code=204)
async def release_worker_table_lease(
    worker_id: str,
    table_id: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
    _: None = Depends(require_worker_coordination_token),
) -> Response:
    if isinstance(repository, FirestoreHandRepository):
        if scope_type not in {"club", "union"} or not scope_id:
            raise HTTPException(status_code=422, detail="Scope type and scope ID are required.")
        if not await asyncio.to_thread(repository.worker_session_is_active, worker_id):
            raise HTTPException(status_code=409, detail="Worker session is not active.")
        runtime = await get_or_restore_scoped_runtime(scope_type, scope_id, table_id)
        current = runtime.current_lease()
        if current is None or current.owner_worker_id != worker_id:
            raise HTTPException(status_code=404, detail="Worker does not own the requested table lease.")
        try:
            released = runtime.release_lease(worker_id, current.fencing_generation)
        except (LeaseAlreadyHeldError, LeaseExpiredError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    else:
        coordinator = require_worker_session_started(worker_id)
        released = coordinator.session.release_table(table_id)
    if not released:
        raise HTTPException(status_code=404, detail="Worker does not own the requested table lease.")
    return Response(status_code=204)


@app.post("/clubs/{club_id}/tables/{table_id}/lease")
async def claim_club_table_lease(
    club_id: str,
    table_id: str,
    request: TableLeaseClaimRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    runtime = get_table(club_id, table_id)
    try:
        lease = runtime.claim_lease(request.worker_id, force_reclaim=request.force_reclaim)
    except LeaseAlreadyHeldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "club_id": club_id,
        "table_id": table_id,
        **table_lease_payload(runtime),
        "owner_worker_id": lease.owner_worker_id,
        "fencing_generation": lease.fencing_generation,
    }


@app.post("/unions/{union_id}/tables/{table_id}/lease")
async def claim_union_table_lease(
    union_id: str,
    table_id: str,
    request: TableLeaseClaimRequest,
    membership: UnionMembership = Depends(require_union_admin()),
) -> dict[str, Any]:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    try:
        lease = runtime.claim_lease(request.worker_id, force_reclaim=request.force_reclaim)
    except LeaseAlreadyHeldError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "union_id": union_id,
        "table_id": table_id,
        **table_lease_payload(runtime),
        "owner_worker_id": lease.owner_worker_id,
        "fencing_generation": lease.fencing_generation,
    }


@app.delete("/unions/{union_id}/tables/{table_id}", status_code=204)
async def delete_union_table(
    union_id: str,
    table_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    key = union_table_key(union_id, table_id)
    runtime = tables.get(key)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    await require_union_table_origin_operator(runtime, current_user)
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
        close_status = await asyncio.to_thread(
            repository.close_union_table_record,
            union_id,
            table_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union table could not be closed.") from error
    if close_status is UnionTableCloseStatus.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Union table metadata not found.")
    if close_status not in {
        UnionTableCloseStatus.CLOSED,
        UnionTableCloseStatus.ALREADY_CLOSED,
    }:
        raise HTTPException(status_code=409, detail="Union table cannot be closed in its current state.")
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
            buy_in_status, seat_staged = await reserve_seat_buy_in(
                runtime,
                runtime.repository.reserve_buy_in,
                (
                    club_id,
                    table_id,
                    request.seat_index,
                    user_id,
                    username,
                    request.buy_in_amount,
                    buy_in_id,
                ),
                request.seat_index,
                user_id,
                username,
                request.buy_in_amount,
                buy_in_id,
                club_id,
                request.accept_big_blind_post,
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
        if buy_in_status == BuyInStatus.TABLE_STATE_CONFLICT:
            raise HTTPException(status_code=409, detail="Table state changed; retry the buy-in request.")

        if not seat_staged and not runtime.game.add_player(
                request.seat_index,
                user_id,
                username,
                request.buy_in_amount,
                buy_in_id,
                club_id=club_id,
                accept_big_blind_post=request.accept_big_blind_post,
            ):
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
            buy_in_status, seat_staged = await reserve_seat_buy_in(
                runtime,
                runtime.repository.reserve_union_buy_in,
                (
                    union_id,
                    table_id,
                    club_id,
                    request.seat_index,
                    user_id,
                    username,
                    request.buy_in_amount,
                    buy_in_id,
                ),
                request.seat_index,
                user_id,
                username,
                request.buy_in_amount,
                buy_in_id,
                club_id,
                request.accept_big_blind_post,
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
        if buy_in_status == BuyInStatus.TABLE_STATE_CONFLICT:
            raise HTTPException(status_code=409, detail="Table state changed; retry the buy-in request.")

        if not seat_staged and not runtime.game.add_player(
            request.seat_index,
            user_id,
            username,
            request.buy_in_amount,
            buy_in_id,
            club_id=club_id,
            accept_big_blind_post=request.accept_big_blind_post,
        ):
            raise HTTPException(status_code=500, detail="Buy-in was reserved but union seat could not be assigned.")
        return runtime.game.get_masked_state_for_player(user_id)


@app.post("/clubs/{club_id}/tables/{table_id}/hands", status_code=201)
async def start_hand(
    club_id: str,
    table_id: str,
    request: StartHandRequest = StartHandRequest(),
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    runtime = get_table(club_id, table_id)
    try:
        started = await runtime.start_hand(request.straddle_player_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not started:
        raise HTTPException(status_code=409, detail="A hand needs at least two seated players and no hand may be running.")
    return runtime.game.get_masked_state_for_player("")


@app.post("/unions/{union_id}/tables/{table_id}/hands", status_code=201)
async def start_union_hand(
    union_id: str,
    table_id: str,
    request: StartHandRequest = StartHandRequest(),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    runtime = tables.get(union_table_key(union_id, table_id))
    if runtime is None:
        raise HTTPException(status_code=404, detail="Union table not found.")
    await require_union_table_origin_operator(runtime, current_user)
    try:
        started = await runtime.start_hand(request.straddle_player_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if not started:
        raise HTTPException(status_code=409, detail="A hand needs at least two seated players and no hand may be running.")
    return runtime.game.get_masked_state_for_player("")


@app.websocket("/ws/table/{club_id}/{table_id}")
async def table_websocket_endpoint(
    websocket: WebSocket,
    club_id: str,
    table_id: str,
) -> None:
    runtime = tables.get((club_id, table_id))
    if isinstance(repository, FirestoreHandRepository) and (
        runtime is None or os.getenv("PUBSUB_ROUTING_ENABLED", "false").lower() == "true"
    ):
        try:
            runtime = await get_or_restore_scoped_runtime("club", club_id, table_id)
        except HTTPException as error:
            await reject_missing_table_socket(websocket, error)
            return
    if runtime is None:
        trace_id = new_trace_id()
        log_error(error_logger, ErrorCode.RESOURCE_NOT_FOUND, trace_id, "Table not found.", level=logging.WARNING)
        await websocket.accept()
        await websocket.send_json(error_body(ErrorCode.RESOURCE_NOT_FOUND, trace_id))
        await websocket.close(
            code=4404,
            reason=ERROR_DEFINITIONS[ErrorCode.RESOURCE_NOT_FOUND].message,
        )
        return
    await authenticate_player_socket(websocket, runtime, club_id)


@app.websocket("/ws/union/{union_id}/{table_id}")
async def union_table_websocket_endpoint(
    websocket: WebSocket,
    union_id: str,
    table_id: str,
) -> None:
    runtime = tables.get(union_table_key(union_id, table_id))
    if isinstance(repository, FirestoreHandRepository) and (
        runtime is None or os.getenv("PUBSUB_ROUTING_ENABLED", "false").lower() == "true"
    ):
        try:
            runtime = await get_or_restore_scoped_runtime("union", union_id, table_id)
        except HTTPException as error:
            await reject_missing_table_socket(websocket, error)
            return
    if runtime is None:
        trace_id = new_trace_id()
        log_error(error_logger, ErrorCode.RESOURCE_NOT_FOUND, trace_id, "Union table not found.", level=logging.WARNING)
        await websocket.accept()
        await websocket.send_json(error_body(ErrorCode.RESOURCE_NOT_FOUND, trace_id))
        await websocket.close(
            code=4404,
            reason=ERROR_DEFINITIONS[ErrorCode.RESOURCE_NOT_FOUND].message,
        )
        return
    await authenticate_player_socket(websocket, runtime, None)


async def reject_missing_table_socket(websocket: WebSocket, error: HTTPException) -> None:
    status_code = error.status_code
    error_code = HTTP_STATUS_CODES.get(status_code, ErrorCode.INTERNAL_SERVER_ERROR)
    trace_id = new_trace_id()
    log_error(error_logger, error_code, trace_id, str(error.detail), level=logging.WARNING)
    await websocket.accept()
    await websocket.send_json(error_body(error_code, trace_id))
    close_code = 4404 if status_code == 404 else 1013
    await websocket.close(
        code=close_code,
        reason=ERROR_DEFINITIONS[error_code].message,
    )


async def authenticate_player_socket(
    websocket: WebSocket,
    runtime: TableRuntime,
    club_id: str | None,
) -> None:
    await websocket.accept()
    registered = False
    protocol_version: str | None = None
    auth_correlation_id: str | None = None
    topic = game_topic(runtime.connection_scope, runtime.game.table_id)
    async def reject_authentication(
        code: ErrorCode,
        close_code: int,
        technical_error: str,
        correlation_id: str | None = None,
    ) -> None:
        trace_id = new_trace_id()
        log_error(error_logger, code, trace_id, technical_error, level=logging.WARNING)
        body = error_body(code, trace_id)
        if correlation_id is not None:
            body["correlation_id"] = correlation_id
        await websocket.send_json(body)
        await websocket.close(code=close_code, reason=ERROR_DEFINITIONS[code].message)

    try:
        try:
            auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except asyncio.TimeoutError:
            await reject_authentication(
                ErrorCode.AUTHENTICATION_FAILED,
                4408,
                "WebSocket authentication timed out.",
            )
            return
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            await reject_authentication(
                ErrorCode.INVALID_FORMAT,
                4400,
                f"Invalid JSON authentication message: {error}",
            )
            return
        if not isinstance(auth_message, dict):
            await reject_authentication(
                ErrorCode.INVALID_FORMAT,
                4401,
                "Authentication message must be an object.",
            )
            return

        requested_club_id = auth_message.get("club_id")
        if "version" in auth_message or "event" in auth_message:
            try:
                auth_event, auth_payload, auth_correlation_id = parse_client_envelope(
                    auth_message,
                    topic,
                )
            except UnsupportedWebSocketVersion as error:
                await reject_authentication(ErrorCode.INVALID_FORMAT, 4406, str(error))
                return
            except ValueError as error:
                await reject_authentication(ErrorCode.INVALID_FORMAT, 4401, str(error))
                return
            if auth_event != "AUTHENTICATE":
                await reject_authentication(
                    ErrorCode.INVALID_FORMAT,
                    4401,
                    "AUTHENTICATE event required.",
                    auth_correlation_id,
                )
                return
            protocol_version = PROTOCOL_VERSION
            id_token = auth_payload.get("id_token")
            requested_club_id = auth_payload.get("club_id")
        else:
            if auth_message.get("type") != "authenticate":
                await reject_authentication(
                    ErrorCode.AUTHENTICATION_FAILED,
                    4401,
                    "Firebase ID token required.",
                )
                return
            id_token = auth_message.get("id_token")
        if not isinstance(id_token, str):
            await reject_authentication(
                ErrorCode.AUTHENTICATION_FAILED,
                4401,
                "Firebase ID token required.",
                auth_correlation_id,
            )
            return

        if runtime.union_id is not None:
            if not isinstance(requested_club_id, str) or not requested_club_id:
                await reject_authentication(
                    ErrorCode.ACCESS_DENIED,
                    4403,
                    "A home club ID is required for union tables.",
                    auth_correlation_id,
                )
                return
            club_id = requested_club_id

        if club_id is None:
            await reject_authentication(
                ErrorCode.ACCESS_DENIED,
                4403,
                "Club membership is required.",
                auth_correlation_id,
            )
            return

        try:
            current_user = await verify_id_token(
                id_token,
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
            error_code = HTTP_STATUS_CODES.get(error.status_code, ErrorCode.INTERNAL_SERVER_ERROR)
            await reject_authentication(error_code, close_code, str(error.detail), auth_correlation_id)
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
            await reject_authentication(
                ErrorCode.ACCESS_DENIED,
                4403,
                "A seated player account is required.",
                auth_correlation_id,
            )
            return

        await connections.connect(
            runtime.connection_scope,
            runtime.game.table_id,
            user_id,
            websocket,
            accepted=True,
            protocol_version=protocol_version,
        )
        registered = True
        if redis_presence is not None:
            await redis_presence.touch(runtime.connection_scope, runtime.game.table_id, user_id)
        initial_state = runtime.game.get_masked_state_for_player(user_id)
        if protocol_version == PROTOCOL_VERSION:
            await websocket.send_json(
                make_envelope(
                    "AUTHENTICATED",
                    {"user_id": user_id},
                    topic,
                    auth_correlation_id,
                )
            )
            await websocket.send_json(
                make_envelope(
                    "TABLE_SNAPSHOT",
                    initial_state,
                    topic,
                    auth_correlation_id,
                )
            )
        else:
            await websocket.send_json({"type": "authenticated", "user_id": user_id})
            await websocket.send_json(initial_state)
        loop = asyncio.get_running_loop()
        last_activity = loop.time()
        next_heartbeat = last_activity + websocket_heartbeat_interval_seconds
        while True:
            now = loop.time()
            idle_remaining = websocket_idle_timeout_seconds - (now - last_activity)
            heartbeat_remaining = next_heartbeat - now
            try:
                raw_action = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=max(0.01, min(idle_remaining, heartbeat_remaining)),
                )
            except asyncio.TimeoutError:
                now = loop.time()
                if now - last_activity >= websocket_idle_timeout_seconds:
                    trace_id = new_trace_id()
                    log_error(
                        error_logger,
                        ErrorCode.SERVICE_UNAVAILABLE,
                        trace_id,
                        "WebSocket connection exceeded its idle timeout.",
                        level=logging.INFO,
                    )
                    await websocket.send_json(
                        error_body(ErrorCode.SERVICE_UNAVAILABLE, trace_id)
                    )
                    await websocket.close(
                        code=4408,
                        reason=ERROR_DEFINITIONS[ErrorCode.SERVICE_UNAVAILABLE].message,
                    )
                    break
                heartbeat_id = str(uuid4())
                if protocol_version == PROTOCOL_VERSION:
                    heartbeat = make_envelope("PING", {}, topic, heartbeat_id)
                else:
                    heartbeat = {"type": "ping", "correlation_id": heartbeat_id}
                await websocket.send_json(heartbeat)
                next_heartbeat = now + websocket_heartbeat_interval_seconds
                continue
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                trace_id = new_trace_id()
                log_error(error_logger, ErrorCode.INVALID_FORMAT, trace_id, error, level=logging.WARNING)
                await websocket.send_json(error_body(ErrorCode.INVALID_FORMAT, trace_id))
                await websocket.close(code=4400, reason=ERROR_DEFINITIONS[ErrorCode.INVALID_FORMAT].message)
                break
            last_activity = loop.time()
            if redis_presence is not None:
                await redis_presence.touch(runtime.connection_scope, runtime.game.table_id, user_id)
            correlation_id: str | None = None
            is_pre_action = False
            try:
                if protocol_version == PROTOCOL_VERSION:
                    event, payload, correlation_id = parse_client_envelope(
                        raw_action,
                        topic,
                    )
                    if event == "PRE_ACTION":
                        is_pre_action = True
                        raw_action = payload
                    elif event == "PLAYER_ACTION":
                        raw_action = payload
                        action_id = payload.get("action_id")
                        if not isinstance(action_id, str) or not action_id or len(action_id) > 128:
                            raise MalformedWebSocketMessage(
                                "PLAYER_ACTION requires a non-empty action_id of at most 128 characters."
                            )
                    elif event == "PONG":
                        continue
                    elif event == "PING":
                        await websocket.send_json(
                            make_envelope("PONG", {}, topic, correlation_id)
                        )
                        continue
                    else:
                        raise MalformedWebSocketMessage(
                            "Event must be PLAYER_ACTION, PRE_ACTION, PING, or PONG."
                        )
                elif isinstance(raw_action, dict) and raw_action.get("type") == "pong":
                    continue
                elif isinstance(raw_action, dict) and raw_action.get("type") == "ping":
                    await websocket.send_json(
                        {"type": "pong", "correlation_id": raw_action.get("correlation_id")}
                    )
                    continue

                if is_pre_action or (
                    protocol_version is None
                    and isinstance(raw_action, dict)
                    and raw_action.get("type") == "pre_action"
                ):
                    option = raw_action.get("action")
                    if option is not None and not isinstance(option, str):
                        raise MalformedWebSocketMessage("Pre-action must be a string or null.")
                    if pubsub_transport is not None and pubsub_transport.gateway_id is not None:
                        result = await request_owner_command(
                            runtime,
                            user_id,
                            {"event": "PRE_ACTION", "option": option},
                        )
                        if not result.get("ok"):
                            if result.get("conflict"):
                                raise ActionIdempotencyConflict("Pre-action request was rejected.")
                            raise ValueError("The owning worker rejected the pre-action.")
                        selected = result.get("action")
                    else:
                        selected = await runtime.set_pre_action(user_id, option)
                    if protocol_version == PROTOCOL_VERSION:
                        response = make_envelope(
                            "PRE_ACTION_SET",
                            {"action": selected},
                            topic,
                            correlation_id,
                        )
                    else:
                        response = {"type": "pre_action_set", "action": selected}
                    await websocket.send_json(response)
                    continue
                request = PlayerAction.model_validate(raw_action)
                action_id = raw_action.get("action_id") if isinstance(raw_action, dict) else None
                if pubsub_transport is not None and pubsub_transport.gateway_id is not None:
                    if action_id is None:
                        action_id = str(uuid4())
                    result = await request_owner_command(
                        runtime,
                        user_id,
                        {
                            "event": "PLAYER_ACTION",
                            "action": request.action,
                            "amount": request.amount,
                            "action_id": action_id,
                        },
                    )
                    if not result.get("ok"):
                        if result.get("conflict"):
                            raise ActionIdempotencyConflict("Action identifier conflict.")
                        raise ValueError("The owning worker rejected the action.")
                    replayed = bool(result.get("replayed"))
                else:
                    replayed = await runtime.act(
                        user_id,
                        request.action,
                        request.amount,
                        action_id,
                    )
                if replayed and protocol_version == PROTOCOL_VERSION:
                    await websocket.send_json(
                        make_envelope(
                            "ACTION_ACKNOWLEDGED",
                            {"action_id": action_id, "duplicate": True},
                            topic,
                            correlation_id,
                        )
                    )
            except ActionIdempotencyConflict as error:
                trace_id = new_trace_id()
                log_error(
                    error_logger,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    trace_id,
                    error,
                    level=logging.WARNING,
                )
                body = error_body(ErrorCode.IDEMPOTENCY_CONFLICT, trace_id)
                response = (
                    make_envelope("ACTION_ERROR", body, topic, correlation_id)
                    if protocol_version == PROTOCOL_VERSION
                    else body
                )
                await websocket.send_json(response)
            except (ValidationError, ValueError) as error:
                code = (
                    ErrorCode.INVALID_FORMAT
                    if isinstance(
                        error,
                        (ValidationError, MalformedWebSocketMessage, UnsupportedWebSocketVersion),
                    )
                    else ErrorCode.REQUEST_CONFLICT
                )
                trace_id = new_trace_id()
                log_error(error_logger, code, trace_id, error, level=logging.WARNING)
                body = error_body(code, trace_id)
                if protocol_version == PROTOCOL_VERSION:
                    response = make_envelope(
                        "ACTION_ERROR",
                        body,
                        topic,
                        correlation_id,
                    )
                else:
                    response = body
                await websocket.send_json(response)
            except Exception as error:
                trace_id = new_trace_id()
                log_error(
                    error_logger,
                    ErrorCode.INTERNAL_SERVER_ERROR,
                    trace_id,
                    error,
                )
                body = error_body(ErrorCode.INTERNAL_SERVER_ERROR, trace_id)
                response = (
                    make_envelope("ACTION_ERROR", body, topic, correlation_id)
                    if protocol_version == PROTOCOL_VERSION
                    else body
                )
                await websocket.send_json(response)
    except WebSocketDisconnect:
        pass
    finally:
        if registered:
            connections.disconnect(
                runtime.connection_scope,
                runtime.game.table_id,
                websocket,
            )
            if redis_presence is not None and not connections.has_user(
                runtime.connection_scope,
                runtime.game.table_id,
                user_id,
            ):
                await redis_presence.remove(
                    runtime.connection_scope,
                    runtime.game.table_id,
                    user_id,
                )