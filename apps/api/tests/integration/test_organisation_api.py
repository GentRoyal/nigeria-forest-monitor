import os
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.api.dependencies import Principal, current_principal
from apps.api.app.db import tenant_connection
from apps.api.app.db.seed import DEPARTMENT_ID, ORGANISATION_ID, OWNER_ID, seed
from apps.api.app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    if os.getenv("NFM_RUN_DB_TESTS") != "1":
        pytest.skip("set NFM_RUN_DB_TESTS=1 to run PostgreSQL integration tests")
    import asyncio

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


def test_organisation_read_and_versioned_update() -> None:
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        current = client.get("/api/v1/organisation", headers=headers)
        assert current.status_code == 200
        assert current.headers["etag"]
        original_name = current.json()["data"]["name"]

        missing_precondition = client.patch(
            "/api/v1/organisation",
            headers=headers,
            json={"name": original_name},
        )
        assert missing_precondition.status_code == 428

        changed = client.patch(
            "/api/v1/organisation",
            headers={**headers, "If-Match": current.headers["etag"]},
            json={"name": "Nigeria Forest Monitoring Pilot"},
        )
        assert changed.status_code == 200
        assert changed.json()["data"]["name"] == "Nigeria Forest Monitoring Pilot"
        assert changed.headers["etag"] != current.headers["etag"]

        stale = client.patch(
            "/api/v1/organisation",
            headers={**headers, "If-Match": current.headers["etag"]},
            json={"name": original_name},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "version_conflict"

        restored = client.patch(
            "/api/v1/organisation",
            headers={**headers, "If-Match": changed.headers["etag"]},
            json={"name": original_name},
        )
        assert restored.status_code == 200


def test_department_create_list_filter_and_case_insensitive_conflict() -> None:
    unique_name = f"Field Coordination {uuid4().hex[:8]}"
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        created = client.post(
            "/api/v1/departments",
            headers=headers,
            json={"name": unique_name},
        )
        assert created.status_code == 201
        assert created.headers["etag"]
        assert created.json()["data"]["status"] == "active"

        duplicate = client.post(
            "/api/v1/departments",
            headers=headers,
            json={"name": unique_name.lower()},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "department_name_conflict"

        listed = client.get("/api/v1/departments?status=active", headers=headers)
        assert listed.status_code == 200
        department_ids = {item["id"] for item in listed.json()["data"]["items"]}
        assert created.json()["data"]["id"] in department_ids


def test_organisation_mutations_require_authentication() -> None:
    with TestClient(app) as client:
        update = client.patch(
            "/api/v1/organisation",
            headers={"If-Match": '"10000000-0000-4000-8000-000000000001:1"'},
            json={"name": "Unauthorised"},
        )
        create = client.post("/api/v1/departments", json={"name": "Unauthorised"})
        assert update.status_code == 401
        assert create.status_code == 401


def test_analyst_can_read_but_cannot_manage_organisation() -> None:
    async def analyst_principal() -> Principal:
        return Principal(
            user_id=UUID("30000000-0000-4000-8000-000000000001"),
            organisation_id=UUID("10000000-0000-4000-8000-000000000001"),
            session_id=uuid4(),
            email="analyst@nfm.local",
            display_name="Test Analyst",
            role="analyst",
            status="active",
            department_id=UUID("20000000-0000-4000-8000-000000000001"),
            department_name="Forest Monitoring",
            timezone="Africa/Lagos",
            teams=(),
        )

    app.dependency_overrides[current_principal] = analyst_principal
    try:
        with TestClient(app) as client:
            readable = client.get("/api/v1/organisation")
            update = client.patch(
                "/api/v1/organisation",
                headers={"If-Match": readable.headers["etag"]},
                json={"name": "Denied update"},
            )
            create = client.post("/api/v1/departments", json={"name": "Denied create"})
            teams = client.get("/api/v1/teams")
            create_team = client.post(
                "/api/v1/teams",
                json={"department_id": str(DEPARTMENT_ID), "name": "Denied team"},
            )

            assert readable.status_code == 200
            assert teams.status_code == 200
            assert update.status_code == 403
            assert update.json()["code"] == "permission_denied"
            assert create.status_code == 403
            assert create.json()["code"] == "permission_denied"
            assert create_team.status_code == 403
            assert create_team.json()["code"] == "permission_denied"
    finally:
        app.dependency_overrides.pop(current_principal, None)


def test_department_and_team_lifecycle() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        department = client.post(
            "/api/v1/departments",
            headers=headers,
            json={"name": f"Regional Operations {suffix}"},
        )
        department_id = department.json()["data"]["id"]
        team = client.post(
            "/api/v1/teams",
            headers=headers,
            json={"department_id": department_id, "name": f"Field Team {suffix}"},
        )
        assert department.status_code == 201
        assert team.status_code == 201

        duplicate = client.post(
            "/api/v1/teams",
            headers=headers,
            json={"department_id": department_id, "name": f"field team {suffix}"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "team_name_conflict"

        occupied = client.patch(
            f"/api/v1/departments/{department_id}",
            headers={**headers, "If-Match": department.headers["etag"]},
            json={"status": "archived"},
        )
        assert occupied.status_code == 409
        assert occupied.json()["code"] == "department_in_use"

        archived_team = client.patch(
            f"/api/v1/teams/{team.json()['data']['id']}",
            headers={**headers, "If-Match": team.headers["etag"]},
            json={"name": f"Field Coordination {suffix}", "status": "archived"},
        )
        assert archived_team.status_code == 200
        assert archived_team.json()["data"]["status"] == "archived"

        archived_department = client.patch(
            f"/api/v1/departments/{department_id}",
            headers={**headers, "If-Match": department.headers["etag"]},
            json={"status": "archived"},
        )
        assert archived_department.status_code == 200
        assert archived_department.json()["data"]["status"] == "archived"

        listed = client.get(
            f"/api/v1/teams?department_id={department_id}&status=archived",
            headers=headers,
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["data"]["items"]] == [
            team.json()["data"]["id"]
        ]


def test_archiving_team_deactivates_memberships() -> None:
    async def add_membership(team_id: UUID) -> None:
        async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
            await connection.execute(
                """
                INSERT INTO team_memberships(organisation_id,team_id,user_id,created_by)
                VALUES (%s,%s,%s,%s)
                """,
                (ORGANISATION_ID, team_id, OWNER_ID, OWNER_ID),
            )

    async def membership_status(team_id: UUID) -> str:
        async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
            row = await (
                await connection.execute(
                    "SELECT status FROM team_memberships WHERE team_id=%s AND user_id=%s",
                    (team_id, OWNER_ID),
                )
            ).fetchone()
        return row["status"]

    import asyncio

    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}
        created = client.post(
            "/api/v1/teams",
            headers=headers,
            json={"department_id": str(DEPARTMENT_ID), "name": f"Review {uuid4().hex[:8]}"},
        )
        team_id = UUID(created.json()["data"]["id"])
        asyncio.run(add_membership(team_id))

        archived = client.patch(
            f"/api/v1/teams/{team_id}",
            headers={**headers, "If-Match": created.headers["etag"]},
            json={"status": "archived"},
        )
        assert archived.status_code == 200
        assert asyncio.run(membership_status(team_id)) == "inactive"
