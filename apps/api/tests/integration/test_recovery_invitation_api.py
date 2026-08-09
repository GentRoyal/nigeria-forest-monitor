import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.db.seed import DEPARTMENT_ID, ORGANISATION_ID, OWNER_ID, seed
from apps.api.app.main import app
from apps.api.app.security.auth import AuthService
from apps.api.app.security.permissions import Role

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    if os.getenv("NFM_RUN_DB_TESTS") != "1":
        pytest.skip("set NFM_RUN_DB_TESTS=1 to run PostgreSQL integration tests")
    asyncio.run(seed())


def test_invitation_summary_acceptance_and_password_reset_lifecycle() -> None:
    service = AuthService()
    email = f"recovery-{uuid4()}@test.local"
    invitation = asyncio.run(
        service.create_invitation(
            organisation_id=ORGANISATION_ID,
            department_id=DEPARTMENT_ID,
            email=email,
            role=Role.ANALYST,
            invited_by=OWNER_ID,
        )
    )

    with TestClient(app) as client:
        summary = client.get(
            f"/api/v1/invitations/{invitation}/summary",
            params={"organisation_slug": "nfm-local-pilot"},
        )
        assert summary.status_code == 200
        assert summary.json()["data"]["masked_email"].endswith("@test.local")
        assert email not in summary.text
        assert summary.json()["data"]["role"] == "analyst"

        weak_password = client.post(
            f"/api/v1/invitations/{invitation}/accept",
            json={
                "organisation_slug": "nfm-local-pilot",
                "display_name": "Recovery Analyst",
                "password": "weak-password",
            },
        )
        assert weak_password.status_code == 422

        accepted = client.post(
            f"/api/v1/invitations/{invitation}/accept",
            json={
                "organisation_slug": "nfm-local-pilot",
                "display_name": "Recovery Analyst",
                "password": "Initial-Forest-2026!",
            },
        )
        assert accepted.status_code == 201
        assert accepted.json()["data"]["next_action"] == "login"

        replay = client.post(
            f"/api/v1/invitations/{invitation}/accept",
            json={
                "organisation_slug": "nfm-local-pilot",
                "display_name": "Replay",
                "password": "Initial-Forest-2026!",
            },
        )
        assert replay.status_code == 400
        assert replay.json()["code"] == "invalid_invitation"

        consumed_summary = client.get(
            f"/api/v1/invitations/{invitation}/summary",
            params={"organisation_slug": "nfm-local-pilot"},
        )
        assert consumed_summary.status_code == 404

        reset_request = client.post(
            "/api/v1/auth/password-resets",
            json={
                "organisation_slug": "nfm-local-pilot",
                "email": email,
            },
        )
        assert reset_request.status_code == 202
        reset_token = reset_request.json()["data"]["development_token"]

        unknown_request = client.post(
            "/api/v1/auth/password-resets",
            json={
                "organisation_slug": "nfm-local-pilot",
                "email": f"unknown-{uuid4()}@test.local",
            },
        )
        assert unknown_request.status_code == 202
        assert unknown_request.json()["data"]["accepted"] is True
        assert "development_token" not in unknown_request.json()["data"]

        completed = client.post(
            "/api/v1/auth/password-resets/complete",
            json={
                "organisation_slug": "nfm-local-pilot",
                "token": reset_token,
                "new_password": "Changed-Forest-2026!",
            },
        )
        assert completed.status_code == 200

        reset_replay = client.post(
            "/api/v1/auth/password-resets/complete",
            json={
                "organisation_slug": "nfm-local-pilot",
                "token": reset_token,
                "new_password": "Changed-Again-2026!",
            },
        )
        assert reset_replay.status_code == 400
        assert reset_replay.json()["code"] == "invalid_reset_token"

        old_password = client.post(
            "/api/v1/auth/login",
            json={
                "organisation_slug": "nfm-local-pilot",
                "email": email,
                "password": "Initial-Forest-2026!",
            },
        )
        assert old_password.status_code == 401

        new_password = client.post(
            "/api/v1/auth/login",
            json={
                "organisation_slug": "nfm-local-pilot",
                "email": email,
                "password": "Changed-Forest-2026!",
            },
        )
        assert new_password.status_code == 200


def test_invalid_invitation_and_reset_do_not_reveal_workspace_state() -> None:
    invalid_token = "x" * 48
    with TestClient(app) as client:
        invitation = client.get(
            f"/api/v1/invitations/{invalid_token}/summary",
            params={"organisation_slug": "unknown-workspace"},
        )
        reset = client.post(
            "/api/v1/auth/password-resets/complete",
            json={
                "organisation_slug": "unknown-workspace",
                "token": invalid_token,
                "new_password": "Changed-Forest-2026!",
            },
        )
        assert invitation.status_code == 404
        assert invitation.json()["code"] == "invalid_invitation"
        assert reset.status_code == 400
        assert reset.json()["code"] == "invalid_reset_token"
