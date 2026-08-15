import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.app.db.seed import DEPARTMENT_ID, seed
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


def test_api_key_lifecycle_scope_enforcement_and_revocation() -> None:
    with TestClient(app) as client:
        bearer = {"Authorization": f"Bearer {login(client)}"}
        created = client.post(
            "/api/v1/api-keys",
            headers=bearer,
            json={"name": f"read-only-{uuid4().hex}", "scopes": ["read"]},
        )
        assert created.status_code == 201, created.text
        key = created.json()["data"]
        assert key["secret"].startswith("nfm_")
        assert key["scopes"] == ["read"]

        machine_headers = {"X-API-Key": key["secret"]}
        readable = client.get("/api/v1/sites", headers=machine_headers)
        assert readable.status_code == 200, readable.text

        forbidden_write = client.post(
            "/api/v1/sites",
            headers=machine_headers,
            json={
                "name": "Should not be created",
                "slug": f"denied-{uuid4().hex}",
                "origin": "custom",
                "managing_department_id": str(DEPARTMENT_ID),
                "boundary": {
                    "geometry": {"type": "Polygon", "coordinates": [[[3, 8], [3.1, 8], [3.1, 8.1], [3, 8.1], [3, 8]]]},
                    "source_authority": "test",
                    "source_identifier": "denied",
                    "licence": "test",
                    "attribution": "test",
                    "source_crs": "EPSG:4326",
                },
            },
        )
        assert forbidden_write.status_code == 403
        assert forbidden_write.json()["code"] == "api_key_scope_denied"

        revoked = client.delete(f"/api/v1/api-keys/{key['id']}", headers=bearer)
        assert revoked.status_code == 200, revoked.text
        rejected = client.get("/api/v1/sites", headers=machine_headers)
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "invalid_api_key"
