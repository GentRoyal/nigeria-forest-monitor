import asyncio
import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.api.dependencies import Principal, current_principal
from apps.api.app.db import tenant_connection
from apps.api.app.db.seed import DEPARTMENT_ID, ORGANISATION_ID, OWNER_ID, TEAM_ID, seed
from apps.api.app.main import app
from apps.api.app.security.passwords import hash_password

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


def create_member(*, department_id: UUID = DEPARTMENT_ID, role: str = "analyst") -> UUID:
    user_id = uuid4()

    async def create() -> None:
        async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
            await connection.execute(
                """
                INSERT INTO user_profiles(
                  id,organisation_id,primary_department_id,email,display_name,
                  role,status,activated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,'active',now())
                """,
                (
                    user_id,
                    ORGANISATION_ID,
                    department_id,
                    f"{user_id}@test.local",
                    "Integration Member",
                    role,
                ),
            )
            await connection.execute(
                """
                INSERT INTO auth_credentials(user_id,organisation_id,password_hash)
                VALUES (%s,%s,%s)
                """,
                (user_id, ORGANISATION_ID, hash_password("Member-Password-2026!")),
            )

    asyncio.run(create())
    return user_id


def test_member_directory_detail_and_versioned_update() -> None:
    user_id = create_member()
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        listed = client.get(f"/api/v1/members?q={user_id}", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 1
        assert listed.json()["data"]["items"][0]["id"] == str(user_id)

        current = client.get(f"/api/v1/members/{user_id}", headers=headers)
        assert current.status_code == 200
        assert current.headers["etag"]

        updated = client.patch(
            f"/api/v1/members/{user_id}",
            headers={**headers, "If-Match": current.headers["etag"]},
            json={"role": "viewer", "reason": "Adjust access for integration test"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["role"] == "viewer"

        stale = client.patch(
            f"/api/v1/members/{user_id}",
            headers={**headers, "If-Match": current.headers["etag"]},
            json={"role": "analyst", "reason": "Exercise stale version protection"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "version_conflict"


def test_team_membership_and_department_change_are_consistent() -> None:
    user_id = create_member()
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        added = client.put(f"/api/v1/teams/{TEAM_ID}/members/{user_id}", headers=headers)
        assert added.status_code == 200
        assert added.json()["data"]["status"] == "active"

        removed = client.delete(f"/api/v1/teams/{TEAM_ID}/members/{user_id}", headers=headers)
        removed_again = client.delete(
            f"/api/v1/teams/{TEAM_ID}/members/{user_id}", headers=headers
        )
        assert removed.status_code == removed_again.status_code == 200

        readded = client.put(f"/api/v1/teams/{TEAM_ID}/members/{user_id}", headers=headers)
        assert readded.status_code == 200

        department = client.post(
            "/api/v1/departments",
            headers=headers,
            json={"name": f"Member Transfer {uuid4().hex[:8]}"},
        )
        current = client.get(f"/api/v1/members/{user_id}", headers=headers)
        moved = client.patch(
            f"/api/v1/members/{user_id}",
            headers={**headers, "If-Match": current.headers["etag"]},
            json={
                "department_id": department.json()["data"]["id"],
                "reason": "Transfer member to a new department",
            },
        )
        assert moved.status_code == 200
        assert moved.json()["data"]["department_id"] == department.json()["data"]["id"]
        assert moved.json()["data"]["teams"] == []

        wrong_department = client.put(
            f"/api/v1/teams/{TEAM_ID}/members/{user_id}", headers=headers
        )
        assert wrong_department.status_code == 409
        assert wrong_department.json()["code"] == "membership_department_mismatch"


def test_last_owner_and_owner_role_are_protected() -> None:
    target_id = create_member(role="analyst")
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}
        owner = client.get(f"/api/v1/members/{OWNER_ID}", headers=headers)

        last_owner = client.patch(
            f"/api/v1/members/{OWNER_ID}",
            headers={**headers, "If-Match": owner.headers["etag"]},
            json={"status": "suspended", "reason": "Exercise last owner protection"},
        )
        assert last_owner.status_code == 409
        assert last_owner.json()["code"] == "last_owner_protected"

        async def administrator_principal() -> Principal:
            return Principal(
                user_id=OWNER_ID,
                organisation_id=ORGANISATION_ID,
                session_id=uuid4(),
                email="administrator@nfm.local",
                display_name="Test Administrator",
                role="administrator",
                status="active",
                department_id=DEPARTMENT_ID,
                department_name="Forest Monitoring",
                timezone="Africa/Lagos",
                teams=(),
            )

        target = client.get(f"/api/v1/members/{target_id}", headers=headers)
        app.dependency_overrides[current_principal] = administrator_principal
        try:
            promoted = client.patch(
                f"/api/v1/members/{target_id}",
                headers={"If-Match": target.headers["etag"]},
                json={"role": "owner", "reason": "Attempt protected role assignment"},
            )
            assert promoted.status_code == 403
            assert promoted.json()["code"] == "owner_role_protected"
        finally:
            app.dependency_overrides.pop(current_principal, None)


def test_analyst_cannot_read_member_directory() -> None:
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
            response = client.get("/api/v1/members")
            assert response.status_code == 403
            assert response.json()["code"] == "permission_denied"
    finally:
        app.dependency_overrides.pop(current_principal, None)
