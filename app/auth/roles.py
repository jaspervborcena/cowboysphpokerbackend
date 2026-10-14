from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.api_core.exceptions import GoogleAPICallError

from database import FirestoreRequiredError
from logic.auth import FirebaseAuthUnavailable, FirebaseTokenVerifier, InvalidFirebaseToken


class ClubRole(str, Enum):
    OWNER = "OWNER"
    MANAGER = "MANAGER"
    AGENT = "AGENT"
    PLAYER = "PLAYER"


class UnionRole(str, Enum):
    UNION_ADMIN = "UNION_ADMIN"


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    display_name: str | None = None
    platform_admin: bool = False


@dataclass(frozen=True)
class ClubMembership:
    user: AuthenticatedUser
    role: ClubRole
    profile: dict[str, Any]


@dataclass(frozen=True)
class UnionMembership:
    user: AuthenticatedUser
    role: UnionRole
    profile: dict[str, Any]


bearer_scheme = HTTPBearer(auto_error=False)


async def verify_id_token(token: str, verifier: FirebaseTokenVerifier) -> AuthenticatedUser:
    try:
        claims = await asyncio.to_thread(verifier.verify_id_token, token)
    except InvalidFirebaseToken as error:
        raise HTTPException(
            status_code=401,
            detail="Firebase ID token is invalid or expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    except FirebaseAuthUnavailable as error:
        raise HTTPException(status_code=503, detail="Firebase authentication is unavailable.") from error

    return AuthenticatedUser(
        uid=claims["uid"],
        display_name=claims.get("name"),
        platform_admin=claims.get("platform_admin") is True,
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await verify_id_token(credentials.credentials, request.app.state.token_verifier)


async def load_active_club_member(
    repository: Any,
    club_id: str,
    current_user: AuthenticatedUser,
) -> ClubMembership:
    try:
        member = await asyncio.to_thread(
            repository.get_club_member,
            club_id,
            current_user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Club membership lookup is unavailable.") from error

    if member is None or member.get("status") != "active":
        raise HTTPException(status_code=403, detail="Active club membership is required.")
    try:
        role = ClubRole(member.get("role"))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="Club membership has no valid role.") from error

    return ClubMembership(current_user, role, member)


async def get_active_club_member(
    request: Request,
    club_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> ClubMembership:
    return await load_active_club_member(
        request.app.state.repository,
        club_id,
        current_user,
    )


async def load_union_admin(
    repository: Any,
    union_id: str,
    current_user: AuthenticatedUser,
) -> UnionMembership:
    try:
        member = await asyncio.to_thread(
            repository.get_union_admin,
            union_id,
            current_user.uid,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union membership lookup is unavailable.") from error

    if member is None or member.get("status") != "active":
        raise HTTPException(status_code=403, detail="Active union administrator membership is required.")
    try:
        role = UnionRole(member.get("role"))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=403, detail="Union membership has no valid role.") from error
    if role is not UnionRole.UNION_ADMIN:
        raise HTTPException(status_code=403, detail="UNION_ADMIN role is required.")

    return UnionMembership(current_user, role, member)


async def get_active_union_admin(
    request: Request,
    union_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> UnionMembership:
    return await load_union_admin(
        request.app.state.repository,
        union_id,
        current_user,
    )


async def load_active_union_club_member(
    repository: Any,
    union_id: str,
    club_id: str,
    current_user: AuthenticatedUser,
) -> ClubMembership:
    membership = await load_active_club_member(repository, club_id, current_user)
    try:
        is_union_club = await asyncio.to_thread(
            repository.is_club_in_union,
            union_id,
            club_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union club membership lookup is unavailable.") from error
    if not is_union_club:
        raise HTTPException(status_code=403, detail="The club is not an active member of this union.")
    return membership


async def get_active_union_club_member(
    request: Request,
    union_id: str,
    club_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> ClubMembership:
    return await load_active_union_club_member(
        request.app.state.repository,
        union_id,
        club_id,
        current_user,
    )


def require_club_role(allowed_roles: list[ClubRole]):
    async def role_dependency(
        membership: ClubMembership = Depends(get_active_club_member),
    ) -> ClubMembership:
        if membership.role not in allowed_roles:
            required = ", ".join(role.value for role in allowed_roles)
            raise HTTPException(
                status_code=403,
                detail=f"One of these club roles is required: {required}.",
            )
        return membership

    return role_dependency


def require_union_admin():
    async def union_admin_dependency(
        membership: UnionMembership = Depends(get_active_union_admin),
    ) -> UnionMembership:
        return membership

    return union_admin_dependency