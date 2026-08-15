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


def site_payload(slug: str, *, sensitivity: str = "normal") -> dict:
    return {
        "name": f"Integration Forest {slug[-8:]}",
        "slug": slug,
        "description": "A production API integration fixture",
        "origin": "custom",
        "sensitivity": sensitivity,
        "managing_department_id": str(DEPARTMENT_ID),
        "tags": ["Priority", "integration", "priority"],
        "boundary": {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[3.0, 8.0], [3.1, 8.0], [3.1, 8.1], [3.0, 8.1], [3.0, 8.0]]],
            },
            "source_authority": "Integration Test Authority",
            "source_identifier": slug,
            "licence": "authorised internal use",
            "attribution": "Integration test fixture",
            "source_crs": "EPSG:4326",
        },
    }


def test_site_create_list_detail_and_versioned_update() -> None:
    slug = f"integration-{uuid4().hex}"
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        created = client.post("/api/v1/sites", headers=headers, json=site_payload(slug))
        assert created.status_code == 201, created.text
        data = created.json()["data"]
        assert data["slug"] == slug
        assert data["current_boundary"]["geometry"]["type"] == "MultiPolygon"
        assert data["current_boundary"]["area_sq_km"] > 0
        assert {tag["name"] for tag in data["tags"]} == {"priority", "integration"}
        site_id = data["id"]
        etag = created.headers["etag"]

        listed = client.get(
            "/api/v1/sites",
            headers=headers,
            params={"q": slug, "tag": "priority", "bbox": "2.9,7.9,3.2,8.2"},
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["data"]] == [site_id]
        assert listed.json()["data"][0]["current_boundary"]["geometry"] is None

        detail = client.get(f"/api/v1/sites/{site_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.headers["etag"] == etag

        missing = client.patch(
            f"/api/v1/sites/{site_id}",
            headers=headers,
            json={"description": "new", "reason": "integration update"},
        )
        assert missing.status_code == 428
        updated = client.patch(
            f"/api/v1/sites/{site_id}",
            headers={**headers, "If-Match": etag},
            json={
                "description": "Updated description",
                "sensitivity": "sensitive",
                "reason": "integration update",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["description"] == "Updated description"
        assert updated.headers["etag"] != etag
        stale = client.patch(
            f"/api/v1/sites/{site_id}",
            headers={**headers, "If-Match": etag},
            json={"description": "stale", "reason": "stale integration update"},
        )
        assert stale.status_code == 409


def test_invalid_boundary_rolls_back_site_and_permissions_hide_raw_sites() -> None:
    slug = f"invalid-{uuid4().hex}"
    invalid = site_payload(slug)
    invalid["boundary"]["geometry"]["coordinates"] = [
        [[3.0, 8.0], [3.1, 8.1], [3.1, 8.0], [3.0, 8.1], [3.0, 8.0]]
    ]
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        rejected = client.post("/api/v1/sites", headers=headers, json=invalid)
        assert rejected.status_code == 422
        assert rejected.json()["code"] == "invalid_geometry"
        accepted = client.post("/api/v1/sites", headers=headers, json=site_payload(slug))
        assert accepted.status_code == 201, accepted.text
        site_id = accepted.json()["data"]["id"]

        sensitive_slug = f"sensitive-{uuid4().hex}"
        sensitive = client.post(
            "/api/v1/sites",
            headers=headers,
            json=site_payload(sensitive_slug, sensitivity="sensitive"),
        )
        assert sensitive.status_code == 201, sensitive.text
        sensitive_id = sensitive.json()["data"]["id"]

        app.dependency_overrides[current_principal] = lambda: Principal(
            user_id=OWNER_ID,
            organisation_id=ORGANISATION_ID,
            session_id=uuid4(),
            email="analyst@nfm.local",
            display_name="Analyst",
            role="analyst",
            status="active",
            department_id=DEPARTMENT_ID,
            department_name="Forest Monitoring",
            timezone="Africa/Lagos",
            teams=(),
        )
        try:
            normal = client.get("/api/v1/sites", params={"q": slug})
            assert [item["id"] for item in normal.json()["data"]] == [site_id]
            assert client.get(f"/api/v1/sites/{site_id}").status_code == 200
            assert client.get(f"/api/v1/sites/{sensitive_id}").status_code == 404
            denied = client.post("/api/v1/sites", json=site_payload(f"analyst-{uuid4().hex}"))
            assert denied.status_code == 403
        finally:
            app.dependency_overrides.clear()

        app.dependency_overrides[current_principal] = lambda: Principal(
            user_id=OWNER_ID,
            organisation_id=ORGANISATION_ID,
            session_id=uuid4(),
            email="viewer@nfm.local",
            display_name="Viewer",
            role="viewer",
            status="active",
            department_id=DEPARTMENT_ID,
            department_name="Forest Monitoring",
            timezone="Africa/Lagos",
            teams=(),
        )
        try:
            assert client.get("/api/v1/sites").json()["data"] == []
            assert client.get(f"/api/v1/sites/{site_id}").status_code == 404
            assert (
                client.post("/api/v1/sites", json=site_payload(f"viewer-{uuid4().hex}")).status_code
                == 403
            )
        finally:
            app.dependency_overrides.clear()
