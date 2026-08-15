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


def replacement_boundary(identifier: str) -> dict:
    return {
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[3.0, 8.0], [3.2, 8.0], [3.2, 8.2], [3.0, 8.2], [3.0, 8.0]]],
        },
        "source_authority": "Integration Test Authority",
        "source_identifier": identifier,
        "licence": "authorised internal use",
        "attribution": "Integration replacement fixture",
        "source_crs": "EPSG:4326",
        "reason": "Authorised boundary correction",
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
            assert client.get(f"/api/v1/sites/{site_id}/boundaries").status_code == 200
            assert client.get(f"/api/v1/sites/{sensitive_id}").status_code == 404
            denied = client.post("/api/v1/sites", json=site_payload(f"analyst-{uuid4().hex}"))
            assert denied.status_code == 403
            denied_boundary = client.post(
                f"/api/v1/sites/{site_id}/boundaries",
                json=replacement_boundary(f"analyst-{uuid4().hex}"),
            )
            assert denied_boundary.status_code == 403
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


def test_boundary_history_replacement_etag_and_immutability() -> None:
    import asyncio

    from psycopg.errors import ObjectNotInPrerequisiteState

    from apps.api.app.db import tenant_connection

    slug = f"boundary-{uuid4().hex}"
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        created = client.post("/api/v1/sites", headers=headers, json=site_payload(slug))
        assert created.status_code == 201, created.text
        site_id = created.json()["data"]["id"]
        first_boundary_id = created.json()["data"]["current_boundary"]["id"]
        original_etag = created.headers["etag"]

        initial = client.get(f"/api/v1/sites/{site_id}/boundaries", headers=headers)
        assert initial.status_code == 200
        assert len(initial.json()["data"]) == 1
        assert initial.json()["data"][0]["geometry"] is None
        assert initial.json()["data"][0]["is_current"] is True

        missing = client.post(
            f"/api/v1/sites/{site_id}/boundaries",
            headers=headers,
            json=replacement_boundary(f"{slug}-v2"),
        )
        assert missing.status_code == 428
        replaced = client.post(
            f"/api/v1/sites/{site_id}/boundaries",
            headers={**headers, "If-Match": original_etag},
            json=replacement_boundary(f"{slug}-v2"),
        )
        assert replaced.status_code == 201, replaced.text
        replacement = replaced.json()["data"]
        assert replacement["version"] == 2
        assert replacement["is_current"] is True
        assert replacement["geometry"]["type"] == "MultiPolygon"
        assert replacement["change_reason"] == "Authorised boundary correction"
        latest_etag = replaced.headers["etag"]
        assert latest_etag != original_etag

        history = client.get(
            f"/api/v1/sites/{site_id}/boundaries",
            headers=headers,
            params={"include_geometry": "true"},
        )
        assert history.status_code == 200, history.text
        versions = history.json()["data"]
        assert [item["version"] for item in versions] == [2, 1]
        assert versions[0]["is_current"] is True
        assert versions[1]["is_current"] is False
        assert versions[1]["superseded_at"] is not None
        assert all(item["geometry"]["type"] == "MultiPolygon" for item in versions)

        first_page = client.get(
            f"/api/v1/sites/{site_id}/boundaries", headers=headers, params={"limit": 1}
        )
        assert [item["version"] for item in first_page.json()["data"]] == [2]
        next_cursor = first_page.json()["meta"]["next_cursor"]
        assert next_cursor
        second_page = client.get(
            f"/api/v1/sites/{site_id}/boundaries",
            headers=headers,
            params={"limit": 1, "cursor": next_cursor},
        )
        assert [item["version"] for item in second_page.json()["data"]] == [1]
        assert second_page.json()["meta"]["next_cursor"] is None

        unchanged = client.post(
            f"/api/v1/sites/{site_id}/boundaries",
            headers={**headers, "If-Match": latest_etag},
            json=replacement_boundary(f"{slug}-same-geometry"),
        )
        assert unchanged.status_code == 409
        assert unchanged.json()["code"] == "boundary_unchanged"
        stale = client.post(
            f"/api/v1/sites/{site_id}/boundaries",
            headers={**headers, "If-Match": original_etag},
            json={
                **replacement_boundary(f"{slug}-v3"),
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[3.0, 8.0], [3.3, 8.0], [3.3, 8.3], [3.0, 8.3], [3.0, 8.0]]],
                },
            },
        )
        assert stale.status_code == 409

    async def mutate_historical_boundary() -> None:
        async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
            await connection.execute(
                "UPDATE site_boundary_versions SET attribution='tampered' WHERE id=%s",
                (first_boundary_id,),
            )

    with pytest.raises(ObjectNotInPrerequisiteState):
        asyncio.run(mutate_historical_boundary())


def test_grid_history_and_viewport_cell_queries() -> None:
    import asyncio

    from apps.api.app.db import tenant_connection

    slug = f"grid-{uuid4().hex}"
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        created = client.post("/api/v1/sites", headers=headers, json=site_payload(slug))
        assert created.status_code == 201, created.text
        site_id = created.json()["data"]["id"]
        grid_id = uuid4()

        async def seed_grid() -> None:
            async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
                await connection.execute(
                    """INSERT INTO grid_versions(
                      id,organisation_id,site_id,version,method,resolution_metres,
                      parameters,creation_reason,processing_compatibility
                    ) VALUES (%s,%s,%s,1,'square',1000,'{}'::jsonb,
                      'Integration grid fixture','v1')""",
                    (grid_id, ORGANISATION_ID, site_id),
                )
                for key, polygon in (
                    ("A-01", "POLYGON((3 8,3.1 8,3.1 8.1,3 8.1,3 8))"),
                    ("A-02", "POLYGON((3.1 8,3.2 8,3.2 8.1,3.1 8.1,3.1 8))"),
                    ("B-01", "POLYGON((4 8,4.1 8,4.1 8.1,4 8.1,4 8))"),
                ):
                    await connection.execute(
                        """INSERT INTO grid_cells(
                          organisation_id,grid_version_id,cell_key,display_label,geometry,area_sq_m
                        ) VALUES (%s,%s,%s,%s,ST_GeomFromText(%s,4326),1000000)""",
                        (ORGANISATION_ID, grid_id, key, f"Cell {key}", polygon),
                    )
                await connection.execute(
                    "UPDATE sites SET current_grid_version_id=%s WHERE id=%s", (grid_id, site_id)
                )

        asyncio.run(seed_grid())

        grids = client.get(f"/api/v1/sites/{site_id}/grids", headers=headers)
        assert grids.status_code == 200, grids.text
        assert len(grids.json()["data"]) == 1
        grid = grids.json()["data"][0]
        assert grid["id"] == str(grid_id)
        assert grid["is_current"] is True
        assert grid["cell_count"] == 3

        unbounded = client.get(f"/api/v1/sites/{site_id}/grid-cells", headers=headers)
        assert unbounded.status_code == 422
        assert unbounded.json()["code"] == "grid_query_filter_required"
        viewport = client.get(
            f"/api/v1/sites/{site_id}/grid-cells",
            headers=headers,
            params={"bbox": "2.95,7.95,3.15,8.15"},
        )
        assert viewport.status_code == 200, viewport.text
        assert {cell["cell_key"] for cell in viewport.json()["data"]} == {"A-01", "A-02"}
        assert all(cell["geometry"]["type"] == "Polygon" for cell in viewport.json()["data"])

        exact = client.get(
            f"/api/v1/sites/{site_id}/grid-cells",
            headers=headers,
            params={"cell_key": "B-01"},
        )
        assert exact.status_code == 200
        assert [cell["cell_key"] for cell in exact.json()["data"]] == ["B-01"]

        first_page = client.get(
            f"/api/v1/sites/{site_id}/grid-cells",
            headers=headers,
            params={"bbox": "2.9,7.9,4.2,8.2", "limit": 1},
        )
        assert first_page.status_code == 200
        cursor = first_page.json()["meta"]["next_cursor"]
        assert cursor
        second_page = client.get(
            f"/api/v1/sites/{site_id}/grid-cells",
            headers=headers,
            params={"bbox": "2.9,7.9,4.2,8.2", "limit": 1, "cursor": cursor},
        )
        assert second_page.status_code == 200
        assert second_page.json()["data"][0]["id"] != first_page.json()["data"][0]["id"]


def test_square_grid_generation_is_versioned_and_immutable() -> None:
    import asyncio

    from psycopg.errors import ObjectNotInPrerequisiteState

    from apps.api.app.db import tenant_connection

    slug = f"generated-grid-{uuid4().hex}"
    generator = {
        "method": "square",
        "resolution_metres": 5_000,
        "clip_to_boundary": True,
        "creation_reason": "Initial monitoring grid",
        "processing_compatibility": "v1",
    }
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        created = client.post("/api/v1/sites", headers=headers, json=site_payload(slug))
        assert created.status_code == 201, created.text
        site_id = created.json()["data"]["id"]
        etag = created.headers["etag"]

        missing = client.post(
            f"/api/v1/sites/{site_id}/grids/generate", headers=headers, json=generator
        )
        assert missing.status_code == 428
        generated = client.post(
            f"/api/v1/sites/{site_id}/grids/generate",
            headers={**headers, "If-Match": etag},
            json=generator,
        )
        assert generated.status_code == 201, generated.text
        first = generated.json()["data"]
        assert first["version"] == 1
        assert first["cell_count"] > 0
        assert first["parameters"]["projection"] == "EPSG:6933"
        assert first["is_current"] is True
        first_grid_id = first["id"]
        updated_etag = generated.headers["etag"]

        cells = client.get(
            f"/api/v1/sites/{site_id}/grid-cells",
            headers=headers,
            params={"grid_version_id": first_grid_id, "bbox": "2.9,7.9,3.2,8.2"},
        )
        assert cells.status_code == 200, cells.text
        assert cells.json()["data"]

        second = client.post(
            f"/api/v1/sites/{site_id}/grids/generate",
            headers={**headers, "If-Match": updated_etag},
            json={**generator, "resolution_metres": 2_500, "creation_reason": "Higher detail grid"},
        )
        assert second.status_code == 201, second.text
        assert second.json()["data"]["version"] == 2
        history = client.get(f"/api/v1/sites/{site_id}/grids", headers=headers)
        assert [grid["version"] for grid in history.json()["data"]] == [2, 1]
        assert history.json()["data"][0]["is_current"] is True
        assert history.json()["data"][1]["superseded_at"] is not None

    async def mutate_grid() -> None:
        async with tenant_connection(ORGANISATION_ID, OWNER_ID) as connection:
            await connection.execute(
                "UPDATE grid_versions SET resolution_metres=999 WHERE id=%s", (first_grid_id,)
            )

    with pytest.raises(ObjectNotInPrerequisiteState):
        asyncio.run(mutate_grid())


def test_schedule_create_read_and_versioned_replace() -> None:
    slug = f"schedule-{uuid4().hex}"
    payload = {
        "cadence": "weekly",
        "sensor_settings": {"preferred_sensors": ["sentinel-1", "sentinel-2"]},
        "quality_settings": {"minimum_coverage": 0.9, "maximum_cloud_cover": 20},
    }
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {login(client)}"}
        created = client.post("/api/v1/sites", headers=headers, json=site_payload(slug))
        assert created.status_code == 201, created.text
        site_id = created.json()["data"]["id"]

        absent = client.get(f"/api/v1/sites/{site_id}/schedule", headers=headers)
        assert absent.status_code == 404
        schedule = client.put(f"/api/v1/sites/{site_id}/schedule", headers=headers, json=payload)
        assert schedule.status_code == 200, schedule.text
        assert schedule.json()["data"]["cadence"] == "weekly"
        assert schedule.json()["data"]["scheduling_version"] == 1
        assert schedule.json()["data"]["sensor_settings"] == payload["sensor_settings"]
        etag = schedule.headers["etag"]

        current = client.get(f"/api/v1/sites/{site_id}/schedule", headers=headers)
        assert current.status_code == 200
        assert current.headers["etag"] == etag
        assert current.json()["data"]["next_due_at"]

        missing = client.put(
            f"/api/v1/sites/{site_id}/schedule",
            headers=headers,
            json={**payload, "cadence": "fortnightly"},
        )
        assert missing.status_code == 428
        updated = client.put(
            f"/api/v1/sites/{site_id}/schedule",
            headers={**headers, "If-Match": etag},
            json={**payload, "cadence": "fortnightly"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["cadence"] == "fortnightly"
        assert updated.json()["data"]["scheduling_version"] == 2
        assert updated.headers["etag"] != etag

        stale = client.put(
            f"/api/v1/sites/{site_id}/schedule",
            headers={**headers, "If-Match": etag},
            json=payload,
        )
        assert stale.status_code == 409
