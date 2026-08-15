import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.api.dependencies import Principal, current_principal
from apps.api.app.db import tenant_connection
from apps.api.app.db.seed import DEPARTMENT_ID, ORGANISATION_ID, OWNER_ID, seed
from apps.api.app.main import app
from apps.api.app.security.audit import record_audit

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


def create_audit_fixtures(action_prefix: str) -> None:
    async def create() -> None:
        async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
            for index in range(3):
                await record_audit(
                    connection,
                    organisation_id=ORGANISATION_ID,
                    actor_id=OWNER_ID,
                    action=f"{action_prefix}.{index}",
                    target_type="integration_fixture",
                    target_id=uuid4(),
                    before={
                        "password": "must-not-leak",
                        "nested": {
                            "refresh_token": "must-not-leak",
                            "safe_value": f"before-{index}",
                        },
                    },
                    after={
                        "geometry": {"coordinates": [1, 2]},
                        "safe_value": f"after-{index}",
                    },
                    reason="audit API integration fixture",
                )

    asyncio.run(create())


def test_audit_query_filters_redacts_and_paginates() -> None:
    action_prefix = f"integration.audit.{uuid4().hex}"
    create_audit_fixtures(action_prefix)
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}

        first = client.get(
            "/api/v1/admin/audit-events",
            headers=headers,
            params={"action_prefix": action_prefix, "limit": 2},
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["data"]["items"]) == 2
        assert first_body["meta"]["has_more"] is True
        assert first_body["meta"]["next_cursor"]
        for item in first_body["data"]["items"]:
            assert item["before_summary"]["password"] == "[REDACTED]"
            assert item["before_summary"]["nested"]["refresh_token"] == "[REDACTED]"
            assert item["before_summary"]["nested"]["safe_value"].startswith("before-")
            assert item["after_summary"]["geometry"] == "[REDACTED]"

        second = client.get(
            "/api/v1/admin/audit-events",
            headers=headers,
            params={
                "action_prefix": action_prefix,
                "limit": 2,
                "cursor": first_body["meta"]["next_cursor"],
            },
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["data"]["items"]) == 1
        assert second_body["meta"]["has_more"] is False
        first_ids = {item["id"] for item in first_body["data"]["items"]}
        assert second_body["data"]["items"][0]["id"] not in first_ids

        wrong_scope = client.get(
            "/api/v1/admin/audit-events",
            headers=headers,
            params={
                "action_prefix": f"{action_prefix}.changed",
                "cursor": first_body["meta"]["next_cursor"],
            },
        )
        assert wrong_scope.status_code == 400
        assert wrong_scope.json()["code"] == "invalid_cursor"


def test_audit_query_rejects_invalid_cursor_and_time_range() -> None:
    with TestClient(app) as client:
        access = login(client)
        headers = {"Authorization": f"Bearer {access}"}
        invalid_cursor = client.get(
            "/api/v1/admin/audit-events",
            headers=headers,
            params={"cursor": "not-a-valid-signed-cursor"},
        )
        invalid_range = client.get(
            "/api/v1/admin/audit-events",
            headers=headers,
            params={
                "created_after": "2026-08-10T12:00:00+01:00",
                "created_before": "2026-08-10T11:00:00+01:00",
            },
        )
        assert invalid_cursor.status_code == 400
        assert invalid_cursor.json()["code"] == "invalid_cursor"
        assert invalid_range.status_code == 422
        assert invalid_range.json()["code"] == "invalid_time_range"


def test_analyst_cannot_access_audit_log() -> None:
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
            response = client.get("/api/v1/admin/audit-events")
            assert response.status_code == 403
            assert response.json()["code"] == "permission_denied"
    finally:
        app.dependency_overrides.pop(current_principal, None)
