from types import SimpleNamespace

import pytest
from firebase_admin import auth
from firebase_admin.exceptions import FirebaseError

from logic import auth as auth_module
from logic.auth import FirebaseAuthUnavailable, FirebaseTokenVerifier, InvalidFirebaseToken


def test_verifier_checks_token_revocation_for_configured_project(monkeypatch) -> None:
    firebase_app = SimpleNamespace(project_id="porpavordelivery")
    calls = {}
    monkeypatch.setattr(auth_module.firebase_admin, "get_app", lambda: firebase_app)

    def verify(token, **kwargs):
        calls["token"] = token
        calls.update(kwargs)
        return {"uid": "firebase-user", "name": "Player"}

    monkeypatch.setattr(auth_module.auth, "verify_id_token", verify)

    claims = FirebaseTokenVerifier("porpavordelivery").verify_id_token("signed-token")

    assert claims["uid"] == "firebase-user"
    assert calls == {
        "token": "signed-token",
        "app": firebase_app,
        "check_revoked": True,
    }


def test_verifier_rejects_invalid_tokens(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_module.firebase_admin,
        "get_app",
        lambda: SimpleNamespace(project_id="porpavordelivery"),
    )

    def reject_token(*args, **kwargs):
        raise auth.InvalidIdTokenError("invalid token")

    monkeypatch.setattr(auth_module.auth, "verify_id_token", reject_token)

    with pytest.raises(InvalidFirebaseToken):
        FirebaseTokenVerifier("porpavordelivery").verify_id_token("invalid-token")


def test_verifier_maps_firebase_service_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        auth_module.firebase_admin,
        "get_app",
        lambda: SimpleNamespace(project_id="porpavordelivery"),
    )

    def fail_verification(*args, **kwargs):
        raise FirebaseError("auth", "certificate service unavailable")

    monkeypatch.setattr(auth_module.auth, "verify_id_token", fail_verification)

    with pytest.raises(FirebaseAuthUnavailable):
        FirebaseTokenVerifier("porpavordelivery").verify_id_token("signed-token")