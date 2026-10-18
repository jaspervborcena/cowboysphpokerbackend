from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
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
    ID_ALLOCATION_ATTEMPTS,
    INITIAL_CLUB_TREASURY_FUNDS,
    WalletTransactionStatus,
    epoch_8_digit_id,
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


class CreateClubRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    owner_player_id: str = Field(pattern=r"^\d{8}$")


class ClubMembershipResponse(BaseModel):
    club_id: str
    club_uuid: str
    user_id: str
    player_id: str
    username: str
    role: Literal["OWNER", "MANAGER", "AGENT", "PLAYER"]


class UserProfileResponse(BaseModel):
    user_id: str
    player_id: str
    username: str
    nickname: str | None = None
    created: bool


class CreateAgentGroupRequest(BaseModel):
    agent_player_id: str = Field(pattern=r"^\d{8}$")


class AgentGroupMemberRequest(BaseModel):
    member_player_id: str = Field(pattern=r"^\d{8}$")


class AgentGroupResponse(BaseModel):
    agent_group_id: str
    club_uuid: str
    club_id: str
    agent_player_id: str
    acting_agent_player_id: str
    agent_balance: int = Field(ge=0)
    rakeback_balance: int = Field(ge=0)
    status: Literal["active", "suspended"]


class AgentGroupMemberResponse(BaseModel):
    membership_id: str
    agent_group_id: str
    club_uuid: str
    club_id: str
    member_player_id: str
    status: Literal["active", "removed"]


class ClubWalletSummaryResponse(BaseModel):
    club_id: str
    club_uuid: str
    user_id: str
    player_id: str
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
    club_uuid: str
    name: str
    owner_user_id: str
    owner_player_id: str
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
    request: Request,
    club_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, Any]:
    profile = membership.profile
    username = str(
        profile.get("username")
        or membership.user.display_name
        or membership.user.uid
    )
    try:
        user_profile, _ = await asyncio.to_thread(
            request.app.state.repository.ensure_user_profile,
            membership.user.uid,
            username,
        )
        club_profile = await asyncio.to_thread(
            request.app.state.repository.get_club_profile,
            club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Membership profile is unavailable.") from error
    if club_profile is None:
        raise HTTPException(status_code=404, detail="Club not found.")
    return {
        "club_id": str(club_profile["club_id"]),
        "club_uuid": club_id,
        "user_id": membership.user.uid,
        "player_id": str(user_profile["player_id"]),
        "username": username,
        "role": membership.role.value,
    }

@router.post(
    "/me/profile/ensure",
    tags=["Membership"],
    summary="Ensure my user profile and player ID",
    description="Idempotently creates the authenticated user's profile and assigns a unique 8-digit player_id. Identity is derived from the verified Firebase ID token.",
    response_model=UserProfileResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        503: error_response("User profile storage is unavailable."),
    },
)
async def ensure_my_user_profile(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    username = current_user.display_name or current_user.uid
    try:
        profile, created = await asyncio.to_thread(
            request.app.state.repository.ensure_user_profile,
            current_user.uid,
            username,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="User profile is unavailable.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="A player ID could not be allocated.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail="User profile identity conflicts with an existing record.") from error
    return {
        "user_id": current_user.uid,
        "player_id": str(profile["player_id"]),
        "username": str(profile.get("username") or username),
        "nickname": profile.get("nickname"),
        "created": created,
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
    request: Request,
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
    username = str(
        profile.get("username")
        or membership.user.display_name
        or membership.user.uid
    )
    try:
        user_profile, _ = await asyncio.to_thread(
            request.app.state.repository.ensure_user_profile,
            membership.user.uid,
            username,
        )
        club_profile = await asyncio.to_thread(
            request.app.state.repository.get_club_profile,
            club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Wallet identity lookup is unavailable.") from error
    if club_profile is None:
        raise HTTPException(status_code=404, detail="Club not found.")
    return {
        "club_id": str(club_profile["club_id"]),
        "club_uuid": club_id,
        "user_id": membership.user.uid,
        "player_id": str(user_profile["player_id"]),
        "role": membership.role.value,
        "currency_type": "CLUB_CHIPS",
        "available_balance": available,
        "in_play_balance": in_play,
    }


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
    create_request: CreateClubRequest,
    platform_admin: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    if not platform_admin.platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access is required.")

    try:
        owner_profile = await asyncio.to_thread(
            request.app.state.repository.get_user_profile_by_player_id,
            create_request.owner_player_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Owner profile lookup is unavailable.") from error
    if owner_profile is None:
        raise HTTPException(status_code=404, detail="Club owner player was not found.")

    club_uuid = str(uuid4())
    owner_user_id = str(owner_profile["user_id"])
    created = False
    club_id = ""
    owner_player_id = create_request.owner_player_id
    for allocation_attempt in range(ID_ALLOCATION_ATTEMPTS):
        club_id = epoch_8_digit_id(allocation_attempt)
        try:
            created, owner_player_id = await asyncio.to_thread(
                request.app.state.repository.create_club,
                club_uuid,
                club_id,
                create_request.name,
                owner_user_id,
                platform_admin.uid,
            )
        except (FirestoreRequiredError, GoogleAPICallError) as error:
            raise HTTPException(status_code=503, detail="Club provisioning is unavailable.") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail="Club provisioning could not be completed.") from error
        if created:
            break
    if not created:
        raise HTTPException(status_code=503, detail="A unique club ID could not be allocated.")
    return {
        "club_id": club_id,
        "club_uuid": club_uuid,
        "name": create_request.name,
        "owner_user_id": owner_user_id,
        "owner_player_id": owner_player_id,
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
    description="Transfers a member's available club chips to the authenticated OWNER, MANAGER, or assigned AGENT. Rejected while the member has chips in play. Requires an idempotency transaction_id.",
    response_model=MemberTransferResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The operator is not authorized for the member."),
        404: error_response("The club member was not found."),
        409: error_response("The member has chips in play, insufficient balance, or a transaction conflict."),
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


@router.post(
    "/clubs/{club_id}/agent-groups",
    status_code=201,
    tags=["Club membership"],
    summary="Create an agent group",
    description="Creates one club-scoped agent group for an active AGENT identified by player_id. The operational balance and rakeback balance start at zero and are separate.",
    response_model=AgentGroupResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("An active OWNER or MANAGER membership is required."),
        404: error_response("Club or active agent membership not found."),
        409: error_response("An agent group already exists for this club and agent."),
        503: error_response("Agent group storage is unavailable."),
    },
)
async def create_agent_group(
    request: Request,
    club_id: str,
    body: CreateAgentGroupRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    try:
        club = await asyncio.to_thread(
            request.app.state.repository.get_club_profile,
            club_id,
        )
        if club is None:
            raise HTTPException(status_code=404, detail="Club not found.")
        group = await asyncio.to_thread(
            request.app.state.repository.create_agent_group,
            club_id,
            str(club["club_id"]),
            str(uuid4()),
            body.agent_player_id,
            membership.user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Agent group storage is unavailable.") from error
    if group is None:
        raise HTTPException(status_code=404, detail="Active AGENT membership not found.")
    return group


@router.get(
    "/clubs/{club_id}/agent-groups/{agent_group_id}",
    tags=["Club membership"],
    summary="Get an agent group",
    response_model=AgentGroupResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The caller is not authorized for this agent group."),
        404: error_response("Agent group not found."),
        503: error_response("Agent group storage is unavailable."),
    },
)
async def get_agent_group(
    request: Request,
    club_id: str,
    agent_group_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> dict[str, Any]:
    if membership.role not in {ClubRole.OWNER, ClubRole.MANAGER, ClubRole.AGENT}:
        raise HTTPException(status_code=403, detail="An OWNER, MANAGER, or assigned AGENT role is required.")
    try:
        group = await asyncio.to_thread(
            request.app.state.repository.get_agent_group,
            agent_group_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Agent group storage is unavailable.") from error
    if group is None or group.get("club_uuid") != club_id:
        raise HTTPException(status_code=404, detail="Agent group not found.")
    if membership.role is ClubRole.AGENT and group.get("acting_agent_user_id") != membership.user.uid:
        raise HTTPException(status_code=403, detail="The caller is not the acting agent for this group.")
    return group


@router.post(
    "/clubs/{club_id}/agent-groups/{agent_group_id}/members",
    status_code=201,
    tags=["Club membership"],
    summary="Assign a player to an agent group",
    response_model=AgentGroupMemberResponse,
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("An active OWNER or MANAGER membership is required."),
        404: error_response("Club, group, or active PLAYER membership not found."),
        503: error_response("Agent group membership storage is unavailable."),
    },
)
async def add_agent_group_member(
    request: Request,
    club_id: str,
    agent_group_id: str,
    body: AgentGroupMemberRequest,
    membership: ClubMembership = Depends(
        require_club_role([ClubRole.OWNER, ClubRole.MANAGER])
    ),
) -> dict[str, Any]:
    try:
        group = await asyncio.to_thread(
            request.app.state.repository.get_agent_group,
            agent_group_id,
        )
        if group is None or group.get("club_uuid") != club_id:
            raise HTTPException(status_code=404, detail="Agent group not found.")
        assignment = await asyncio.to_thread(
            request.app.state.repository.add_agent_group_member,
            club_id,
            str(group["club_id"]),
            agent_group_id,
            body.member_player_id,
            membership.user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Agent group membership storage is unavailable.") from error
    if assignment is None:
        raise HTTPException(status_code=404, detail="Active PLAYER membership not found.")
    return assignment


@router.get(
    "/clubs/{club_id}/agent-groups/{agent_group_id}/members",
    tags=["Club membership"],
    summary="List players assigned to an agent group",
    response_model=list[AgentGroupMemberResponse],
    responses={
        401: error_response("Missing or invalid Firebase ID token."),
        403: error_response("The caller is not authorized for this agent group."),
        404: error_response("Agent group not found."),
        503: error_response("Agent group membership storage is unavailable."),
    },
)
async def list_agent_group_members(
    request: Request,
    club_id: str,
    agent_group_id: str,
    membership: ClubMembership = Depends(get_active_club_member),
) -> list[dict[str, Any]]:
    if membership.role not in {ClubRole.OWNER, ClubRole.MANAGER, ClubRole.AGENT}:
        raise HTTPException(status_code=403, detail="An OWNER, MANAGER, or assigned AGENT role is required.")
    try:
        group = await asyncio.to_thread(
            request.app.state.repository.get_agent_group,
            agent_group_id,
        )
        if group is None or group.get("club_uuid") != club_id:
            raise HTTPException(status_code=404, detail="Agent group not found.")
        if membership.role is ClubRole.AGENT and group.get("acting_agent_user_id") != membership.user.uid:
            raise HTTPException(status_code=403, detail="The caller is not the acting agent for this group.")
        return await asyncio.to_thread(
            request.app.state.repository.list_agent_group_members,
            agent_group_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Agent group membership storage is unavailable.") from error