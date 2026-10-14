from __future__ import annotations

from typing import Any

import firebase_admin
from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError


class InvalidFirebaseToken(Exception):
    pass


class FirebaseAuthUnavailable(Exception):
    pass


class FirebaseTokenVerifier:
    def __init__(self, project_id: str | None) -> None:
        self.project_id = project_id

    def verify_id_token(self, token: str) -> dict[str, Any]:
        if not self.project_id:
            raise FirebaseAuthUnavailable("FIREBASE_PROJECT_ID must be configured.")

        try:
            try:
                firebase_app = firebase_admin.get_app()
            except ValueError:
                firebase_app = firebase_admin.initialize_app(
                    options={"projectId": self.project_id}
                )
            if firebase_app.project_id != self.project_id:
                raise FirebaseAuthUnavailable(
                    "Firebase Admin is configured for a different project."
                )
            claims = auth.verify_id_token(token, app=firebase_app, check_revoked=True)
        except (
            auth.InvalidIdTokenError,
            auth.ExpiredIdTokenError,
            auth.RevokedIdTokenError,
        ) as error:
            raise InvalidFirebaseToken("Firebase ID token is invalid or expired.") from error
        except FirebaseError as error:
            raise FirebaseAuthUnavailable("Firebase token verification failed.") from error

        if not claims.get("uid"):
            raise InvalidFirebaseToken("Firebase ID token does not contain a user ID.")
        return claims