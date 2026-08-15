from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import system_connection, tenant_connection
from ..security.tokens import decode_access_token, hash_opaque_token
from .errors import ApiError

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    organisation_id: UUID
    session_id: UUID
    email: str
    display_name: str
    role: str
    status: str
    department_id: UUID
    department_name: str
    timezone: str
    teams: tuple[dict[str, str], ...]
    api_key_id: UUID | None = None
    api_scopes: frozenset[str] = frozenset()


async def find_organisation_id(slug: str) -> UUID | None:
    normalised = slug.strip().lower()
    async with system_connection() as connection:
        await connection.execute(
            "SELECT set_config('app.login_organisation_slug',%s,true)",
            (normalised,),
        )
        organisation = await (
            await connection.execute(
                """
                SELECT id,status FROM organisations
                WHERE slug=%s
                """,
                (normalised,),
            )
        ).fetchone()
    if not organisation or organisation["status"] != "active":
        return None
    return organisation["id"]


async def resolve_organisation_id(slug: str) -> UUID:
    organisation_id = await find_organisation_id(slug)
    if organisation_id is None:
        raise ApiError(
            401,
            "invalid_credentials",
            "Authentication failed",
            "The supplied credentials are invalid.",
        )
    return organisation_id


async def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    if x_api_key:
        if credentials:
            raise ApiError(400, "ambiguous_authentication", "Ambiguous authentication", "Use either Bearer authentication or X-API-Key, not both.")
        return await _api_key_principal(request, x_api_key)
    if not credentials or credentials.scheme.lower() != "bearer":
        raise ApiError(
            401,
            "authentication_required",
            "Authentication required",
            "A valid Bearer access token is required.",
            {"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = UUID(str(claims["sub"]))
        organisation_id = UUID(str(claims["org"]))
        session_id = UUID(str(claims["sid"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
        raise ApiError(
            401,
            "invalid_access_token",
            "Authentication failed",
            "The access token is invalid or expired.",
            {"WWW-Authenticate": "Bearer"},
        ) from error

    async with tenant_connection(organisation_id, user_id) as connection:
        row = await (
            await connection.execute(
                """
                SELECT
                  u.id,u.email::text email,u.display_name,u.role,u.status,u.timezone,
                  u.primary_department_id,d.name department_name,
                  COALESCE(
                    jsonb_agg(
                      jsonb_build_object('id',t.id::text,'name',t.name)
                    ) FILTER (WHERE t.id IS NOT NULL),
                    '[]'::jsonb
                  ) teams
                FROM auth_sessions s
                JOIN user_profiles u
                  ON u.organisation_id=s.organisation_id AND u.id=s.user_id
                JOIN departments d
                  ON d.organisation_id=u.organisation_id
                 AND d.id=u.primary_department_id
                LEFT JOIN team_memberships tm
                  ON tm.organisation_id=u.organisation_id
                 AND tm.user_id=u.id AND tm.status='active'
                LEFT JOIN teams t
                  ON t.organisation_id=tm.organisation_id
                 AND t.id=tm.team_id AND t.status='active'
                WHERE s.id=%s AND s.user_id=%s
                  AND s.revoked_at IS NULL AND s.expires_at>now()
                  AND u.status='active'
                GROUP BY u.id,d.name
                """,
                (session_id, user_id),
            )
        ).fetchone()
    if not row:
        raise ApiError(
            401,
            "session_expired",
            "Session unavailable",
            "The session is expired or revoked.",
            {"WWW-Authenticate": "Bearer"},
        )
    request.state.organisation_id = str(organisation_id)
    request.state.actor_id = str(user_id)
    return Principal(
        user_id=user_id,
        organisation_id=organisation_id,
        session_id=session_id,
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        status=row["status"],
        department_id=row["primary_department_id"],
        department_name=row["department_name"],
        timezone=row["timezone"],
        teams=tuple(row["teams"]),
    )


async def _api_key_principal(request: Request, raw_key: str) -> Principal:
    """Resolve an API key to its accountable active user and enforce its scope."""
    if not raw_key.startswith("nfm_") or len(raw_key) < 40:
        raise ApiError(401, "invalid_api_key", "Authentication failed", "The API key is invalid, expired, or revoked.", {"WWW-Authenticate": "ApiKey"})
    secret_hash = hash_opaque_token(raw_key)
    async with system_connection() as connection:
        await connection.execute("SELECT set_config('app.api_key_secret_hash',%s,true)", (secret_hash,))
        key = await (await connection.execute(
            """SELECT id,organisation_id,accountable_user_id,scopes FROM api_keys
            WHERE secret_hash=%s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())""",
            (secret_hash,),
        )).fetchone()
        if not key:
            raise ApiError(401, "invalid_api_key", "Authentication failed", "The API key is invalid, expired, or revoked.", {"WWW-Authenticate": "ApiKey"})
    async with tenant_connection(key["organisation_id"], key["accountable_user_id"]) as connection:
        profile = await (await connection.execute(
            """SELECT u.email::text email,u.display_name,u.role,u.status,u.timezone,u.primary_department_id,d.name department_name
            FROM user_profiles u JOIN departments d ON d.organisation_id=u.organisation_id AND d.id=u.primary_department_id
            WHERE u.id=%s AND u.status='active'""",
            (key["accountable_user_id"],),
        )).fetchone()
        if not profile:
            raise ApiError(401, "invalid_api_key", "Authentication failed", "The API key is invalid, expired, or revoked.", {"WWW-Authenticate": "ApiKey"})
        teams = await (await connection.execute(
            """SELECT t.id::text id,t.name FROM team_memberships tm JOIN teams t ON t.id=tm.team_id
            WHERE tm.organisation_id=%s AND tm.user_id=%s AND tm.status='active' AND t.status='active' ORDER BY t.name""",
            (key["organisation_id"], key["accountable_user_id"]),
        )).fetchall()
        await connection.execute("UPDATE api_keys SET last_used_at=now(),usage_metadata=jsonb_set(usage_metadata,ARRAY['request_count'],to_jsonb(COALESCE((usage_metadata->>'request_count')::bigint,0)+1),true) WHERE id=%s", (key["id"],))
    scopes = frozenset(key["scopes"])
    required_scope = "read" if request.method in {"GET", "HEAD", "OPTIONS"} else "export" if request.url.path == "/api/v1/exports" else "write"
    if request.url.path.startswith("/api/v1/api-keys") or required_scope not in scopes:
        raise ApiError(403, "api_key_scope_denied", "API key scope denied", "This API key is not authorised for this operation.")
    request.state.organisation_id = str(key["organisation_id"])
    request.state.actor_id = str(key["accountable_user_id"])
    return Principal(
        user_id=key["accountable_user_id"], organisation_id=key["organisation_id"], session_id=UUID(int=0),
        email=profile["email"], display_name=profile["display_name"], role=profile["role"], status=profile["status"],
        department_id=profile["primary_department_id"], department_name=profile["department_name"], timezone=profile["timezone"],
        teams=tuple(teams), api_key_id=key["id"], api_scopes=scopes,
    )
