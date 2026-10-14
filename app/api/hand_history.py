from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from google.api_core.exceptions import GoogleAPICallError

from app.auth.roles import (
    AuthenticatedUser,
    ClubMembership,
    bearer_scheme,
    get_current_user,
    get_active_union_club_member,
    load_active_club_member,
    load_active_union_club_member,
)
from database import FirestoreRequiredError


router = APIRouter()


async def get_replay_membership(
    request: Request,
    club_id: str,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ClubMembership:
    try:
        current_user: AuthenticatedUser = await get_current_user(request, credentials)
        return await load_active_club_member(
            request.app.state.repository,
            club_id,
            current_user,
        )
    except HTTPException as error:
        if error.status_code in {401, 403}:
            raise HTTPException(
                status_code=403,
                detail="Authenticated active club membership is required.",
            ) from error
        raise


@router.get("/clubs/{club_id}/hands/{hand_id}/replay")
async def get_hand_replay(
    request: Request,
    club_id: str,
    hand_id: str,
    membership: ClubMembership = Depends(get_replay_membership),
) -> dict[str, Any]:
    repository = request.app.state.repository
    try:
        hand_data = await asyncio.to_thread(
            repository.get_hand_history,
            club_id,
            hand_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Hand history is unavailable.") from error
    if hand_data is None:
        raise HTTPException(status_code=404, detail="Hand history not found.")

    return filter_replay_for_user(hand_data, membership.user.uid)


def filter_replay_for_user(
    hand_data: dict[str, Any], requesting_user_id: str
) -> dict[str, Any]:
    """Return replay-safe fields and mask cards not public to this viewer."""
    is_showdown = bool(hand_data.get("is_showdown", False))
    allowed_frame_fields = (
        "frame_index",
        "timestamp",
        "street",
        "community_cards",
        "pot",
        "active_seat",
        "last_action",
    )
    allowed_player_fields = (
        "user_id",
        "username",
        "club_id",
        "seat",
        "stack",
        "mucked",
        "in_hand",
    )
    filtered_frames = []

    for frame in hand_data.get("frames", []):
        filtered_frame = {
            field: frame[field]
            for field in allowed_frame_fields
            if field in frame
        }
        filtered_players = []
        for player in frame.get("players", []):
            is_self = player.get("user_id") == requesting_user_id
            should_reveal_cards = is_self or (
                is_showdown
                and not player.get("mucked", False)
                and player.get("in_hand", False)
            )
            filtered_player = {
                field: player[field]
                for field in allowed_player_fields
                if field in player
            }
            filtered_player["hole_cards"] = (
                list(player.get("hole_cards", []))
                if should_reveal_cards
                else ["??", "??"]
            )
            filtered_players.append(filtered_player)
        filtered_frame["players"] = filtered_players
        filtered_frames.append(filtered_frame)

    return {
        "hand_id": hand_data["hand_id"],
        "timestamp": hand_data.get("timestamp"),
        "final_board": list(hand_data.get("final_board", [])),
        "pot_total": hand_data.get("pot_total", 0),
        "is_showdown": is_showdown,
        "winners": list(hand_data.get("winners", [])),
        "total_frames": len(filtered_frames),
        "frames": filtered_frames,
    }


@router.get("/unions/{union_id}/hands/{hand_id}/replay")
async def get_union_hand_replay(
    request: Request,
    union_id: str,
    hand_id: str,
    club_id: str,
    membership: ClubMembership = Depends(get_active_union_club_member),
) -> dict[str, Any]:
    repository = request.app.state.repository
    try:
        hand_data = await asyncio.to_thread(
            repository.get_union_hand_history,
            union_id,
            hand_id,
        )
    except (FirestoreRequiredError, GoogleAPICallError) as error:
        raise HTTPException(status_code=503, detail="Union hand history is unavailable.") from error
    if hand_data is None:
        raise HTTPException(status_code=404, detail="Union hand history not found.")

    return filter_replay_for_user(hand_data, membership.user.uid)