from apps.api.app.main import app


def test_first_authentication_batch_is_published_in_openapi() -> None:
    document = app.openapi()
    assert {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/password-resets",
        "/api/v1/auth/password-resets/complete",
        "/api/v1/invitations/{token}/summary",
        "/api/v1/invitations/{token}/accept",
        "/api/v1/me",
    }.issubset(document["paths"])
    assert "HTTPBearer" in document["components"]["securitySchemes"]


def test_internal_authentication_fields_are_not_in_token_response() -> None:
    schema = app.openapi()["components"]["schemas"]["AccessTokenData"]
    properties = schema["properties"]
    assert "refresh_token" not in properties
    assert "role" not in properties
    assert "email" not in properties


def test_self_service_profile_and_session_endpoints_are_published() -> None:
    document = app.openapi()
    assert "patch" in document["paths"]["/api/v1/me"]
    assert "get" in document["paths"]["/api/v1/me/sessions"]
    assert "delete" in document["paths"]["/api/v1/me/sessions/{session_id}"]

    session_properties = document["components"]["schemas"]["SessionData"]["properties"]
    assert "refresh_token_hash" not in session_properties
    assert "token_family_id" not in session_properties


def test_organisation_and_department_batch_is_published() -> None:
    document = app.openapi()
    assert {"get", "patch"}.issubset(document["paths"]["/api/v1/organisation"])
    assert {"get", "post"}.issubset(document["paths"]["/api/v1/departments"])

    organisation_update = document["components"]["schemas"]["OrganisationUpdateRequest"]
    assert "slug" not in organisation_update["properties"]
    assert "status" not in organisation_update["properties"]


def test_department_lifecycle_and_team_batch_is_published() -> None:
    document = app.openapi()
    assert "patch" in document["paths"]["/api/v1/departments/{department_id}"]
    assert {"get", "post"}.issubset(document["paths"]["/api/v1/teams"])
    assert "patch" in document["paths"]["/api/v1/teams/{team_id}"]


def test_member_administration_batch_is_published() -> None:
    document = app.openapi()
    assert "get" in document["paths"]["/api/v1/members"]
    assert {"get", "patch"}.issubset(document["paths"]["/api/v1/members/{user_id}"])
    membership_path = document["paths"]["/api/v1/teams/{team_id}/members/{user_id}"]
    assert {"put", "delete"}.issubset(membership_path)
