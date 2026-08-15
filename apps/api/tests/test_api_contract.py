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


def test_invitation_administration_and_logout_all_are_published() -> None:
    document = app.openapi()
    assert "post" in document["paths"]["/api/v1/auth/logout-all"]
    assert {"get", "post"}.issubset(document["paths"]["/api/v1/invitations"])
    assert "delete" in document["paths"]["/api/v1/invitations/{invitation_id}"]

    invitation = document["components"]["schemas"]["InvitationData"]["properties"]
    assert "token_hash" not in invitation


def test_audit_query_is_published_without_secret_storage_fields() -> None:
    document = app.openapi()
    assert "get" in document["paths"]["/api/v1/admin/audit-events"]
    event = document["components"]["schemas"]["AuditEventData"]["properties"]
    assert "token_hash" not in event
    assert "password_hash" not in event


def test_first_site_management_batch_is_published() -> None:
    document = app.openapi()
    assert {"get", "post"}.issubset(document["paths"]["/api/v1/sites"])
    assert {"get", "patch"}.issubset(document["paths"]["/api/v1/sites/{site_id}"])

    create = document["components"]["schemas"]["SiteCreateRequest"]["properties"]
    assert "boundary" in create
    assert "created_by" not in create
    boundary = document["components"]["schemas"]["BoundaryCreateRequest"]["properties"]
    assert "checksum" not in boundary
    assert "validation_result" not in boundary


def test_boundary_version_batch_is_published() -> None:
    document = app.openapi()
    path = document["paths"]["/api/v1/sites/{site_id}/boundaries"]
    assert {"get", "post"}.issubset(path)

    request = document["components"]["schemas"]["BoundaryVersionCreateRequest"]["properties"]
    assert "reason" in request
    assert "checksum" not in request
    boundary = document["components"]["schemas"]["BoundaryData"]["properties"]
    assert {"created_by", "change_reason", "superseded_at", "is_current"}.issubset(boundary)


def test_grid_read_batch_is_published() -> None:
    document = app.openapi()
    assert "get" in document["paths"]["/api/v1/sites/{site_id}/grids"]
    assert "get" in document["paths"]["/api/v1/sites/{site_id}/grid-cells"]
    cell = document["components"]["schemas"]["GridCellData"]["properties"]
    assert {"cell_key", "geometry", "area_sq_m"}.issubset(cell)


def test_grid_generation_batch_is_published() -> None:
    document = app.openapi()
    assert "post" in document["paths"]["/api/v1/sites/{site_id}/grids/generate"]
    request = document["components"]["schemas"]["GridGenerateRequest"]["properties"]
    assert {"method", "resolution_metres", "clip_to_boundary", "creation_reason"}.issubset(request)


def test_initial_schedule_batch_is_published() -> None:
    document = app.openapi()
    assert {"get", "put"}.issubset(document["paths"]["/api/v1/sites/{site_id}/schedule"])
    schedule = document["components"]["schemas"]["ScheduleData"]["properties"]
    assert {"cadence", "next_due_at", "scheduling_version", "status"}.issubset(schedule)


def test_schedule_lifecycle_batch_is_published() -> None:
    document = app.openapi()
    assert "post" in document["paths"]["/api/v1/sites/{site_id}/schedule/suspend"]
    assert "post" in document["paths"]["/api/v1/sites/{site_id}/schedule/resume"]
    assert "delete" in document["paths"]["/api/v1/sites/{site_id}/schedule"]
