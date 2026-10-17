from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.wallets import router as wallet_router
from app.errors import install_error_handlers
from database import create_hand_repository
from logic.auth import FirebaseTokenVerifier


def create_app() -> FastAPI:
    service = FastAPI(
        title="Cowboys PH Poker Wallet API",
        version="1.0.0",
        openapi_tags=[
            {
                "name": "Membership",
                "description": "Firebase-authenticated member profile and club-chip balance reads.",
            },
            {
                "name": "Global wallet",
                "description": "The signed-in user's global play-money wallet. This version grants 20,000 directly and does not debit a platform reserve.",
            },
            {
                "name": "Club wallets",
                "description": "Authorized club treasury and member chip operations. These balances are separate from global play money.",
            },
            {
                "name": "Club provisioning",
                "description": "Platform-admin-only club creation and initial treasury funding.",
            },
            {
                "name": "Service",
                "description": "Unauthenticated service health check.",
            },
        ],
    )
    install_error_handlers(service, "cowboys.wallet_api.errors")

    origins = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:4200,http://127.0.0.1:4200",
        ).split(",")
        if origin.strip()
    ]
    service.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    service.state.repository = create_hand_repository(
        os.getenv("FIRESTORE_ENABLED", "false").lower() == "true"
    )
    service.state.token_verifier = FirebaseTokenVerifier(
        os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    )
    service.include_router(wallet_router)

    @service.get("/health", tags=["Service"], summary="Check service health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "wallet-api"}

    return service


app = create_app()