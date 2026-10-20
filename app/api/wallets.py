from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from google.api_core.exceptions import GoogleAPICallError
from pydantic import BaseModel, Field

from app.auth.roles import (
    AuthenticatedUser,
    ClubMembership,
    ClubRole,
    get_active_club_member,
    get_current_user,
    require_club_role,
)
from database import (
    FirestoreRequiredError,
    INITIAL_CLUB_TREASURY_FUNDS,
    RoleChangeStatus,
    WalletTransactionStatus,
)


router = APIRouter()


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


class ClubRoleChangeRequest(BaseModel):
    role: Literal["MANAGER", "AGENT", "PLAYER"]
    transaction_id: UUID


class CreateClubRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PlatformCreateClubRequest(CreateClubRequest):
    owner_user_id: str = Field(min_length=1, max_length=128)


class ClubMembershipResponse(BaseModel):
    club_id: str
    user_id: str
    username: str
    role: Literal["OWNER", "MANAGER", "AGENT", "PLAYER"]


class ClubRoleChangeResponse(BaseModel):
    club_id: str
    user_id: str
    previous_role: Literal["MANAGER", "AGENT", "PLAYER"]
    role: Literal["MANAGER", "AGENT", "PLAYER"]
    transaction_id: str
    status: Literal["completed", "idempotent_replay"]


class ClubMemberDirectoryEntry(BaseModel):
    user_id: str
    username: str
    role: Literal["OWNER", "MANAGER", "AGENT", "PLAYER"]


class ClubWalletSummaryResponse(BaseModel):
    club_id: str
    user_id: str
    role: Literal["OWNER", "MANAGER", "AGENT", "PLAYER"]
    currency_type: Literal["CLUB_CHIPS"]
    available_balance: int = Field(ge=0)
    in_play_balance: int = Field(ge=0)


class GlobalWalletResponse(BaseModel):
    wallet_id: str
    wallet_type: Literal["GLOBAL_PLAYMONEY"]
    currency_type: Literal["GLOBAL_PLAYMONEY"]
    user_id: str
    balance: int = Field(ge=0)


class GlobalWalletEnsureResponse(GlobalWalletResponse):
    created: bool


class ClubProvisionResponse(BaseModel):
    club_id: str
    name: str
    owner_user_id: str
    treasury_balance: int = Field(ge=0)


class ClubTreasuryResponse(BaseModel):
    club_id: str
    currency_type: Literal["CLUB_CHIPS"]
    chip_balance: int = Field(ge=0)


class InterClubTransferResponse(BaseModel):
    transaction_id: str
    type: Literal["INTER_CLUB_TRANSFER"]
    from_club_id: str
    to_club_id: str
    amount: int = Field(gt=0)
    status: Literal["completed", "idempotent_replay"]


class MemberTransferResponse(BaseModel):
    club_id: str
    transaction_id: str
    type: Literal["SEND_OUT", "CLAIM_BACK"]
    amount: int = Field(gt=0)
    from_user_id: str
    to_user_id: str
    status: Literal["completed", "idempotent_replay"]


class ApiErrorDetailResponse(BaseModel):
    message: str
    field: str | None = None


class ApiErrorResponse(BaseModel):
    status: Literal["error", "fail"]
    code: int
    message: str
    errors: list[ApiErrorDetailResponse]
    trace_id: str


def error_response(description: str) -> dict[str, Any]:
    return {"model": ApiErrorResponse, "description": description}


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
    if status is WalletTransactionStatus.INSUFFICIENT_BALANCE:
        return HTTPException(status_code=409, detail="Insufficient available club chip balance.")
    if status is WalletTransactionStatus.INVALID_BALANCE:
        return HTTPException(status_code=409, detail="Club wallet balance is invalid.")
    if status is WalletTransactionStatus.INVALID_AMOUNT:
        return HTTPException(status_code=422, detail="Transfer amount must be a positive integer.")
    if status is WalletTransactionStatus.IDEMPOTENCY_CONFLICT:
        return HTTPException(status_code=409, detail="transaction_id was already used for a different transfer.")
    return HTTPException(status_code=500, detail="Club wallet transaction could not be completed.")


@router.get(
    "/clubs/{club_id}/members/me",
    tags=["Membership"],
    summary="Get my club membership",
    description="Returns the authenticated user's active membership profile and server-assigned role.",
    response_model=ClubMembershipResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The authenticated user is not an active club member."),
        503: error_response("Membership storage is unavailable."),
    },
)
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


@router.get(
    "/clubs/{club_id}/members/me/wallet",
    tags=["Membership"],
    summary="Get my club wallet summary",
    description="Returns this member's available and in-play club chips. Requires active club membership; these balances are not separate by agent/player role.",
    response_model=ClubWalletSummaryResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The authenticated user is not an active club member."),
        409: error_response("Stored member balances are invalid."),
        503: error_response("Membership storage is unavailable."),
    },
)
async def get_my_club_wallet_summary(
    club_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, Any]:
    profile = membership.profile
    available = profile.get("chip_balance", 0)
    in_play = profile.get("in_play_balance", 0)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (available, in_play)
    ):
        raise HTTPException(status_code=409, detail="Club member wallet balance is invalid.")
    return {
        "club_id": club_id,
        "user_id": membership.user.uid,
        "role": membership.role.value,
        "currency_type": "CLUB_CHIPS",
        "available_balance": available,
        "in_play_balance": in_play,
    }


@router.get(
    "/clubs/{club_id}/members",
    tags=["Membership"],
    summary="List active club members",
    description="Lists active members for club OWNER and MANAGER role administration. Wallet balances are not included.",
    response_model=list[ClubMemberDirectoryEntry],
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("An active OWNER or MANAGER membership is required."),
        503: error_response("Membership storage is unavailable."),
    },
)
async def list_club_members(
    club_id: str,
    request: Request,
    membership: ClubMembership = Depends(get_active_club_member),
) -> list[dict[str, str]]:
    if membership.role not in {ClubRole.OWNER, ClubRole.MANAGER}:
        raise HTTPException(status_code=403, detail="Only club owners and managers can list members.")
    try:
        members = await asyncio.to_thread(
            request.app.state.repository.list_club_members,
            club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Membership storage is unavailable.") from error
    return sorted(members, key=lambda member: (member["username"].casefold(), member["user_id"]))


@router.post(
    "/clubs/{club_id}/members/{user_id}/role",
    tags=["Membership"],
    summary="Change a club member role",
    description="Applies owner/manager role transition rules and writes a role-change audit entry atomically.",
    response_model=ClubRoleChangeResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The actor is not authorized for this role change."),
        404: error_response("The club or target member was not found."),
        409: error_response("The role change conflicts with the current membership or transaction."),
        503: error_response("Role storage is unavailable."),
    },
)
async def change_club_member_role(
    club_id: str,
    user_id: str,
    change_request: ClubRoleChangeRequest,
    request: Request,
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, str]:
    if membership.role not in {ClubRole.OWNER, ClubRole.MANAGER}:
        raise HTTPException(status_code=403, detail="Only club owners and managers can change member roles.")
    try:
        status, previous_role = await asyncio.to_thread(
            request.app.state.repository.change_club_member_role,
            club_id,
            membership.user.uid,
            user_id,
            change_request.role,
            str(change_request.transaction_id),
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Role storage is unavailable.") from error

    if status in {RoleChangeStatus.COMPLETED, RoleChangeStatus.IDEMPOTENT_REPLAY}:
        return {
            "club_id": club_id,
            "user_id": user_id,
            "previous_role": previous_role or change_request.role,
            "role": change_request.role,
            "transaction_id": str(change_request.transaction_id),
            "status": status.value,
        }
    if status is RoleChangeStatus.CLUB_NOT_FOUND:
        raise HTTPException(status_code=404, detail="Club not found.")
    if status is RoleChangeStatus.TARGET_NOT_FOUND:
        raise HTTPException(status_code=404, detail="Club member not found.")
    if status is RoleChangeStatus.TARGET_INACTIVE:
        raise HTTPException(status_code=409, detail="Inactive club members cannot have their role changed.")
    if status is RoleChangeStatus.IDEMPOTENCY_CONFLICT:
        raise HTTPException(status_code=409, detail="transaction_id was already used for a different role change.")
    if status is RoleChangeStatus.SELF_CHANGE:
        raise HTTPException(status_code=403, detail="Members cannot change their own role.")
    if status is RoleChangeStatus.OWNER_PROTECTED:
        raise HTTPException(status_code=403, detail="The OWNER role cannot be changed through this route.")
    if status is RoleChangeStatus.INVALID_TRANSITION:
        raise HTTPException(status_code=403, detail="This actor cannot make the requested role transition.")
    raise HTTPException(status_code=403, detail="The actor is not authorized for this role change.")


@router.post(
    "/me/wallets/global-playmoney/ensure",
    tags=["Global wallet"],
    summary="Ensure my global play-money wallet",
    description="Atomically and idempotently creates a wallet for the authenticated user with a 20,000-chip initial grant debited from the platform reserve.",
    response_model=GlobalWalletEnsureResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        409: error_response("Stored wallet balance is invalid."),
        503: error_response("Wallet storage is unavailable."),
    },
)
async def ensure_my_global_playmoney_wallet(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        wallet, created = await asyncio.to_thread(
            request.app.state.repository.ensure_global_wallet,
            current_user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Global wallet provisioning is unavailable.") from error
    balance = wallet.get("chip_balance")
    if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
        raise HTTPException(status_code=409, detail="Global wallet balance is invalid.")
    return {
        "wallet_id": wallet["wallet_id"],
        "wallet_type": "GLOBAL_PLAYMONEY",
        "currency_type": "GLOBAL_PLAYMONEY",
        "user_id": current_user.uid,
        "balance": balance,
        "created": created,
    }


@router.get(
    "/me/wallets/global-playmoney",
    tags=["Global wallet"],
    summary="Get my global play-money wallet",
    description="Reads the authenticated user's wallet without creating it. No client-supplied user ID is accepted.",
    response_model=GlobalWalletResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        404: error_response("The authenticated user has no global wallet."),
        409: error_response("Stored wallet balance is invalid."),
        503: error_response("Wallet storage is unavailable."),
    },
)
async def get_my_global_playmoney_wallet(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        wallet = await asyncio.to_thread(
            request.app.state.repository.get_global_wallet,
            current_user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Global wallet is unavailable.") from error
    if wallet is None:
        raise HTTPException(status_code=404, detail="Global wallet not found.")
    balance = wallet.get("chip_balance")
    if isinstance(balance, bool) or not isinstance(balance, int) or balance < 0:
        raise HTTPException(status_code=409, detail="Global wallet balance is invalid.")
    return {
        "wallet_id": wallet["wallet_id"],
        "wallet_type": "GLOBAL_PLAYMONEY",
        "currency_type": "GLOBAL_PLAYMONEY",
        "user_id": current_user.uid,
        "balance": balance,
    }


@router.post(
    "/clubs",
    status_code=201,
    tags=["Club provisioning"],
    summary="Create a club",
    description="Creates a club, grants 10,000 CLUB_CHIPS from the club giveaway reserve, and makes the authenticated Firebase user its OWNER.",
    response_model=ClubProvisionResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        409: error_response("The generated club ID conflicts with an existing club."),
        503: error_response("Club storage is unavailable."),
    },
)
async def create_self_service_club(
    request: Request,
    create_request: CreateClubRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    club_id = str(uuid4())
    try:
        created = await asyncio.to_thread(
            request.app.state.repository.create_club,
            club_id,
            create_request.name,
            current_user.uid,
            current_user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club creation is unavailable.") from error
    if not created:
        raise HTTPException(status_code=409, detail="Club ID already exists; retry the request.")
    return {
        "club_id": club_id,
        "name": create_request.name,
        "owner_user_id": current_user.uid,
        "treasury_balance": INITIAL_CLUB_TREASURY_FUNDS,
    }


@router.post(
    "/platform/clubs",
    status_code=201,
    tags=["Club provisioning"],
    summary="Create a club",
    description="Platform-admin operation. Atomically creates an owner membership and funds the club treasury with 10,000 club chips debited from the platform reserve.",
    response_model=ClubProvisionResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The user is not a platform administrator."),
        409: error_response("The generated club ID conflicts with an existing club."),
        503: error_response("Club storage is unavailable."),
    },
)
async def create_club(
    request: Request,
    create_request: PlatformCreateClubRequest,
    platform_admin: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    if not platform_admin.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access is required.")

    club_id = str(uuid4())
    try:
        created = await asyncio.to_thread(
            request.app.state.repository.create_club,
            club_id,
            create_request.name,
            create_request.owner_user_id,
            platform_admin.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club provisioning is unavailable.") from error
    if not created:
        raise HTTPException(status_code=409, detail="Club ID already exists; retry the request.")
    return {
        "club_id": club_id,
        "name": create_request.name,
        "owner_user_id": create_request.owner_user_id,
        "treasury_balance": INITIAL_CLUB_TREASURY_FUNDS,
    }


@router.get(
    "/clubs/{club_id}/wallet",
    tags=["Club wallets"],
    summary="Get club treasury balance",
    description="Returns the club treasury balance. Only active OWNER and MANAGER members can read it.",
    response_model=ClubTreasuryResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("An active OWNER or MANAGER membership is required."),
        409: error_response("Stored treasury balance is invalid."),
        503: error_response("Club wallet storage is unavailable."),
    },
)
async def get_club_wallet(
    request: Request,
    club_id: str,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    try:
        balance = await asyncio.to_thread(
            request.app.state.repository.get_club_wallet_balance,
            club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club wallet is unavailable.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="Club wallet balance is invalid.") from error
    return {"club_id": club_id, "currency_type": "CLUB_CHIPS", "chip_balance": balance}


@router.post(
    "/clubs/{club_id}/wallet/transfers",
    tags=["Club wallets"],
    summary="Transfer between club treasuries",
    description="Transfers club chips to another club atomically. Requires an active OWNER or MANAGER membership and an idempotency transaction_id.",
    response_model=InterClubTransferResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("An active OWNER or MANAGER membership is required."),
        404: error_response("The source or destination club was not found."),
        409: error_response("The transfer conflicts with current balances or an existing transaction."),
        422: error_response("The transfer request is invalid."),
        503: error_response("Club wallet storage is unavailable."),
    },
)
async def transfer_between_club_wallets(
    request: Request,
    club_id: str,
    transfer: ClubTreasuryTransferRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    operator_id = membership.user.uid
    try:
        status = await asyncio.to_thread(
            request.app.state.repository.transfer_between_club_wallets,
            club_id,
            transfer.destination_club_id,
            operator_id,
            transfer.amount,
            str(transfer.transaction_id),
            operator_id,
            transfer.note,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club wallet is unavailable.") from error
    if status not in {
        WalletTransactionStatus.COMPLETED,
        WalletTransactionStatus.IDEMPOTENT_REPLAY,
    }:
        raise wallet_transaction_http_error(status)
    return {
        "transaction_id": str(transfer.transaction_id),
        "type": "INTER_CLUB_TRANSFER",
        "from_club_id": club_id,
        "to_club_id": transfer.destination_club_id,
        "amount": transfer.amount,
        "status": status.value,
    }


@router.post(
    "/clubs/{club_id}/wallet/send-out",
    tags=["Club wallets"],
    summary="Send club chips to a member",
    description="Transfers club chips from the authenticated OWNER, MANAGER, or assigned AGENT to a member. Requires an idempotency transaction_id.",
    response_model=MemberTransferResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The operator is not authorized for the recipient."),
        404: error_response("The club member was not found."),
        409: error_response("Insufficient balance or transaction conflict."),
        422: error_response("The transfer request is invalid."),
        503: error_response("Club wallet storage is unavailable."),
    },
)
async def send_out_club_chips(
    request: Request,
    club_id: str,
    transfer: ClubWalletTransferRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER, ClubRole.AGENT])
    ),
) -> dict[str, Any]:
    operator_id = membership.user.uid
    try:
        status = await asyncio.to_thread(
            request.app.state.repository.send_out_club_chips,
            club_id,
            operator_id,
            transfer.user_id,
            transfer.amount,
            str(transfer.transaction_id),
            operator_id,
            transfer.note,
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
        "transaction_id": str(transfer.transaction_id),
        "type": "SEND_OUT",
        "amount": transfer.amount,
        "from_user_id": operator_id,
        "to_user_id": transfer.user_id,
        "status": status.value,
    }


@router.post(
    "/clubs/{club_id}/wallet/claim-back",
    tags=["Club wallets"],
    summary="Claim club chips back from a member",
    description="Transfers only a member's available club chips to the authenticated OWNER, MANAGER, or assigned AGENT. In-play chips are never claimed; the member may keep chips in play while available chips are claimed. Requires an idempotency transaction_id.",
    response_model=MemberTransferResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The operator is not authorized for the member."),
        404: error_response("The club member was not found."),
        409: error_response("The member has insufficient available balance or the transaction conflicts."),
        422: error_response("The transfer request is invalid."),
        503: error_response("Club wallet storage is unavailable."),
    },
)
async def claim_back_club_chips(
    request: Request,
    club_id: str,
    transfer: ClubWalletTransferRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER, ClubRole.AGENT])
    ),
) -> dict[str, Any]:
    operator_id = membership.user.uid
    try:
        status = await asyncio.to_thread(
            request.app.state.repository.claim_back_club_chips,
            club_id,
            transfer.user_id,
            operator_id,
            transfer.amount,
            str(transfer.transaction_id),
            operator_id,
            transfer.note,
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
        "transaction_id": str(transfer.transaction_id),
        "type": "CLAIM_BACK",
        "amount": transfer.amount,
        "from_user_id": transfer.user_id,
        "to_user_id": operator_id,
        "status": status.value,
    }


@router.get(
    "/clubs/{club_id}/rake/players",
    tags=["Club rake reports"],
    summary="List player rake and winnings summaries",
    description="Returns per-player rake and hand-result summaries to club owners/managers; assigned agents see only their active downline. Players cannot view rake reports.",
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("An owner, manager, or assigned agent role is required."),
        503: error_response("Club rake report storage is unavailable."),
    },
)
async def get_club_player_rake_report(
    club_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    before_total_rake_generated: int | None = Query(default=None, ge=0),
    before_user_id: str | None = Query(default=None),
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, Any]:
    if (before_total_rake_generated is None) != (before_user_id is None):
        raise HTTPException(
            status_code=422,
            detail=(
                "Both before_total_rake_generated and before_user_id are "
                "required together."
            ),
        )
    if membership.role is ClubRole.PLAYER:
        raise HTTPException(status_code=403, detail="Players cannot view club rake reports.")
    agent_user_id = (
        membership.user.uid if membership.role is ClubRole.AGENT else None
    )
    try:
        report = await asyncio.to_thread(
            request.app.state.repository.get_club_player_rake_report,
            club_id,
            agent_user_id=agent_user_id,
            limit=limit,
            before_total_rake_generated=before_total_rake_generated,
            before_user_id=before_user_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club rake report is unavailable.") from error
    summary_fields = (
        "hands_played",
        "rake_eligible_hands",
        "hands_with_award",
        "positive_net_hands",
        "total_contestable_contribution",
        "total_gross_awards",
        "total_payout_after_fees",
        "total_net_result",
        "total_rake_generated",
    )
    response: dict[str, Any] = {
        "club_id": club_id,
        "scope": report["scope"],
        "rake_generated_total": report["rake_generated_total"],
        "next_before_total_rake_generated": report[
            "next_before_total_rake_generated"
        ],
        "next_before_user_id": report["next_before_user_id"],
        "players": [
            {
                "user_id": player["user_id"],
                "username": player.get("username", player["user_id"]),
                **{
                    field: int(player.get(field, 0))
                    for field in summary_fields
                },
                "last_hand_id": player.get("last_hand_id"),
                "last_hand_at": player.get("last_hand_at"),
            }
            for player in report["players"]
        ],
    }
    if membership.role in {ClubRole.OWNER, ClubRole.MANAGER}:
        response.update(
            {
                "total_rake_collected": report["total_rake_collected"],
                "attributed_rake_total": report["attributed_rake_total"],
                "unattributed_rake_total": report["unattributed_rake_total"],
            }
        )
    return response


@router.get(
    "/clubs/{club_id}/rake/players/{user_id}/hands",
    tags=["Club rake reports"],
    summary="List a player's rake and hand results",
    description="Returns per-hand contribution, rake attribution, gross award, payout, and net result. Available to club owners/managers and the player's currently assigned agent; players cannot view these records.",
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The actor is not authorized to view this player's rake report."),
        404: error_response("The club member was not found."),
        503: error_response("Player rake history is unavailable."),
    },
)
async def get_player_rake_hand_history(
    club_id: str,
    user_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    before_timestamp: str | None = Query(default=None),
    before_hand_id: str | None = Query(default=None),
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, Any]:
    if (before_timestamp is None) != (before_hand_id is None):
        raise HTTPException(
            status_code=422,
            detail="Both before_timestamp and before_hand_id are required together.",
        )
    if membership.role is ClubRole.AGENT:
        try:
            member = await asyncio.to_thread(
                request.app.state.repository.get_club_member,
                club_id,
                user_id,
            )
        except (FirestoreRequiredError, GoogleAPICallError) as error:
            raise HTTPException(status_code=503, detail="Player rake history is unavailable.") from error
        if (
            member is None
            or member.get("status") != "active"
            or member.get("agent_id") != membership.user.uid
        ):
            raise HTTPException(status_code=403, detail="The player is not assigned to this agent.")
    elif membership.role not in {ClubRole.OWNER, ClubRole.MANAGER}:
        raise HTTPException(status_code=403, detail="Players cannot view rake reports.")

    try:
        history = await asyncio.to_thread(
            request.app.state.repository.get_player_rake_hand_records,
            club_id,
            user_id,
            limit=limit,
            before_timestamp=before_timestamp,
            before_hand_id=before_hand_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Player rake history is unavailable.") from error
    return {"club_id": club_id, "user_id": user_id, **history}