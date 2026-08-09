import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from ..settings import get_settings


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_opaque_token(token: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.password_pepper.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def issue_access_token(
    *,
    user_id: UUID,
    organisation_id: UUID,
    session_id: UUID,
) -> tuple[str, datetime]:
    settings = get_settings()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "org": str(organisation_id),
        "sid": str(session_id),
        "iat": now,
        "exp": expires_at,
        "iss": "nigeria-forest-monitor",
        "aud": "nfm-api",
    }
    return (
        jwt.encode(payload, settings.access_token_secret, algorithm="HS256"),
        expires_at,
    )


def decode_access_token(token: str) -> dict[str, object]:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.access_token_secret,
        algorithms=["HS256"],
        audience="nfm-api",
        issuer="nigeria-forest-monitor",
        leeway=timedelta(seconds=settings.jwt_leeway_seconds),
    )
