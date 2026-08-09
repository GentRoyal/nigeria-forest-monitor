import os

import pytest
from fastapi.testclient import TestClient

from apps.api.app.db.seed import seed
from apps.api.app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    if os.getenv("NFM_RUN_DB_TESTS") != "1":
        pytest.skip("set NFM_RUN_DB_TESTS=1 to run PostgreSQL integration tests")
    import asyncio

    asyncio.run(seed())


def login(client: TestClient):
    return client.post(
        "/api/v1/auth/login",
        json={
            "organisation_slug": "nfm-local-pilot",
            "email": "owner@nfm.local",
            "password": "LocalForest!2026",
        },
    )


def test_login_refresh_me_and_logout_flow() -> None:
    with TestClient(app) as client:
        login_response = login(client)
        assert login_response.status_code == 200
        assert login_response.json()["data"]["token_type"] == "Bearer"
        assert "HttpOnly" in login_response.headers["set-cookie"]
        first_access = login_response.json()["data"]["access_token"]

        profile = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {first_access}"},
        )
        assert profile.status_code == 200
        assert profile.json()["data"]["email"] == "owner@nfm.local"
        assert profile.json()["data"]["role"] == "owner"
        assert profile.json()["data"]["department_name"] == "Forest Monitoring"

        rejected_refresh = client.post(
            "/api/v1/auth/refresh",
            json={"organisation_slug": "nfm-local-pilot"},
        )
        assert rejected_refresh.status_code == 403
        assert rejected_refresh.json()["code"] == "csrf_validation_failed"

        csrf_token = client.cookies.get("nfm_csrf_token")
        refreshed = client.post(
            "/api/v1/auth/refresh",
            json={"organisation_slug": "nfm-local-pilot"},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert refreshed.status_code == 200
        second_access = refreshed.json()["data"]["access_token"]
        assert second_access != first_access

        old_session = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {first_access}"},
        )
        assert old_session.status_code == 401
        assert old_session.json()["code"] == "session_expired"

        logout = client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {second_access}"},
        )
        assert logout.status_code == 200
        assert logout.json()["data"]["success"] is True

        logged_out = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {second_access}"},
        )
        assert logged_out.status_code == 401
        assert logged_out.json()["code"] == "session_expired"


def test_local_refresh_body_fallback() -> None:
    with TestClient(app) as browser:
        login_response = login(browser)
        assert login_response.status_code == 200
        raw_refresh = browser.cookies.get("nfm_refresh_token")

    with TestClient(app) as openapi_client:
        refreshed = openapi_client.post(
            "/api/v1/auth/refresh",
            json={
                "organisation_slug": "nfm-local-pilot",
                "refresh_token": raw_refresh,
            },
        )
        assert refreshed.status_code == 200


def test_authentication_errors_are_generic_problem_documents() -> None:
    with TestClient(app) as client:
        unknown_workspace = client.post(
            "/api/v1/auth/login",
            json={
                "organisation_slug": "unknown-workspace",
                "email": "owner@nfm.local",
                "password": "LocalForest!2026",
            },
        )
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={
                "organisation_slug": "nfm-local-pilot",
                "email": "owner@nfm.local",
                "password": "incorrect-password",
            },
        )
        assert unknown_workspace.status_code == wrong_password.status_code == 401
        assert unknown_workspace.json()["code"] == wrong_password.json()["code"]
        assert unknown_workspace.json()["detail"] == wrong_password.json()["detail"]
        assert unknown_workspace.headers["content-type"].startswith("application/problem+json")


def test_missing_bearer_and_invalid_request_id_are_handled() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/me",
            headers={"X-Request-ID": "not-a-uuid"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "authentication_required"
        assert response.headers["X-Request-ID"] != "not-a-uuid"


def test_profile_update_and_session_management() -> None:
    with TestClient(app) as client:
        first_login = login(client)
        second_login = login(client)
        first_access = first_login.json()["data"]["access_token"]
        second_access = second_login.json()["data"]["access_token"]
        second_session_id = second_login.json()["data"]["session_id"]

        updated = client.patch(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {first_access}"},
            json={"display_name": "Local Forest Owner", "timezone": "Africa/Lagos"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["display_name"] == "Local Forest Owner"

        sessions = client.get(
            "/api/v1/me/sessions",
            headers={"Authorization": f"Bearer {first_access}"},
        )
        assert sessions.status_code == 200
        items = sessions.json()["data"]["items"]
        assert len(items) >= 2
        assert sum(item["current"] for item in items) == 1
        assert all("refresh_token_hash" not in item for item in items)

        revoked = client.delete(
            f"/api/v1/me/sessions/{second_session_id}",
            headers={"Authorization": f"Bearer {first_access}"},
        )
        assert revoked.status_code == 200

        rejected = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {second_access}"},
        )
        assert rejected.status_code == 401
        assert rejected.json()["code"] == "session_expired"


def test_profile_update_validates_payload_and_session_ownership() -> None:
    with TestClient(app) as client:
        access = login(client).json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {access}"}

        empty_update = client.patch("/api/v1/me", headers=headers, json={})
        invalid_timezone = client.patch(
            "/api/v1/me",
            headers=headers,
            json={"timezone": "not/a-real-timezone"},
        )
        missing_session = client.delete(
            "/api/v1/me/sessions/00000000-0000-0000-0000-000000000000",
            headers=headers,
        )

        assert empty_update.status_code == 422
        assert invalid_timezone.status_code == 422
        assert missing_session.status_code == 404
        assert missing_session.json()["code"] == "session_not_found"


def test_logout_all_revokes_every_active_session() -> None:
    with TestClient(app) as client:
        first = login(client).json()["data"]["access_token"]
        second = login(client).json()["data"]["access_token"]

        logged_out = client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {first}"},
        )
        assert logged_out.status_code == 200
        assert logged_out.json()["data"]["success"] is True

        first_rejected = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {first}"},
        )
        second_rejected = client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {second}"},
        )
        assert first_rejected.status_code == second_rejected.status_code == 401
        assert first_rejected.json()["code"] == "session_expired"
        assert second_rejected.json()["code"] == "session_expired"
