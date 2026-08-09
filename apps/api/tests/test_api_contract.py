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
