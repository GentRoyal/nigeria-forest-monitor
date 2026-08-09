import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.api.dependencies import Principal, current_principal
from apps.api.app.db.seed import DEPARTMENT_ID, ORGANISATION_ID, OWNER_ID, seed
from apps.api.app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    if os.getenv("NFM_RUN_DB_TESTS") != "1":
        pytest.skip("set NFM_RUN_DB_TESTS=1 to run PostgreSQL integration tests")
    asyncio.run(seed())


def login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "organisation_slug": "nfm-local-pilot",
            "email": "owner@nfm.local",
            "password": "LocalForest!2026",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def test_invitation_create_list_revoke_and_reinvite() -> None:
    email = f"invite-{uuid4()}@test.local"
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        created = client.post(
            "/api/v1/invitations",
            headers=headers,
            json={
                "department_id": str(DEPARTMENT_ID),
                "email": email,
                "role": "analyst",
            },
        )
        assert created.status_code == 201
        token = created.json()["data"]["development_token"]
        invitation_id = created.json()["data"]["id"]
        assert token
        assert "token_hash" not in created.text

        listed = client.get("/api/v1/invitations?status=pending", headers=headers)
        assert listed.status_code == 200
        assert invitation_id in {item["id"] for item in listed.json()["data"]["items"]}
        assert "token_hash" not in listed.text

        summary = client.get(
            f"/api/v1/invitations/{token}/summary",
            params={"organisation_slug": "nfm-local-pilot"},
        )
        assert summary.status_code == 200

        revoked = client.delete(f"/api/v1/invitations/{invitation_id}", headers=headers)
        revoked_again = client.delete(
            f"/api/v1/invitations/{invitation_id}", headers=headers
        )
        assert revoked.status_code == revoked_again.status_code == 200

        unavailable = client.get(
            f"/api/v1/invitations/{token}/summary",
            params={"organisation_slug": "nfm-local-pilot"},
        )
        assert unavailable.status_code == 404

        reinvited = client.post(
            "/api/v1/invitations",
            headers=headers,
            json={
                "department_id": str(DEPARTMENT_ID),
                "email": email,
                "role": "viewer",
            },
        )
        assert reinvited.status_code == 201
        assert reinvited.json()["data"]["id"] != invitation_id


def test_invitation_rejects_existing_member_and_owner_role() -> None:
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        existing = client.post(
            "/api/v1/invitations",
            headers=headers,
            json={
                "department_id": str(DEPARTMENT_ID),
                "email": "owner@nfm.local",
                "role": "administrator",
            },
        )
        owner_role = client.post(
            "/api/v1/invitations",
            headers=headers,
            json={
                "department_id": str(DEPARTMENT_ID),
                "email": f"owner-role-{uuid4()}@test.local",
                "role": "owner",
            },
        )
        assert existing.status_code == 409
        assert existing.json()["code"] == "member_exists"
        assert owner_role.status_code == 422


def test_accepted_invitation_cannot_be_revoked() -> None:
    email = f"accepted-{uuid4()}@test.local"
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}
        created = client.post(
            "/api/v1/invitations",
            headers=headers,
            json={
                "department_id": str(DEPARTMENT_ID),
                "email": email,
                "role": "viewer",
            },
        )
        token = created.json()["data"]["development_token"]
        accepted = client.post(
            f"/api/v1/invitations/{token}/accept",
            json={
                "organisation_slug": "nfm-local-pilot",
                "display_name": "Accepted Viewer",
                "password": "Accepted-Viewer-2026!",
            },
        )
        assert accepted.status_code == 201

        revoke = client.delete(
            f"/api/v1/invitations/{created.json()['data']['id']}",
            headers=headers,
        )
        assert revoke.status_code == 409
        assert revoke.json()["code"] == "invitation_already_accepted"


def test_analyst_cannot_administer_invitations() -> None:
    async def analyst_principal() -> Principal:
        return Principal(
            user_id=OWNER_ID,
            organisation_id=ORGANISATION_ID,
            session_id=uuid4(),
            email="analyst@nfm.local",
            display_name="Test Analyst",
            role="analyst",
            status="active",
            department_id=DEPARTMENT_ID,
            department_name="Forest Monitoring",
            timezone="Africa/Lagos",
            teams=(),
        )

    app.dependency_overrides[current_principal] = analyst_principal
    try:
        with TestClient(app) as client:
            listed = client.get("/api/v1/invitations")
            created = client.post(
                "/api/v1/invitations",
                json={
                    "department_id": str(DEPARTMENT_ID),
                    "email": f"denied-{uuid4()}@test.local",
                    "role": "viewer",
                },
            )
            assert listed.status_code == 403
            assert created.status_code == 403
    finally:
        app.dependency_overrides.pop(current_principal, None)
