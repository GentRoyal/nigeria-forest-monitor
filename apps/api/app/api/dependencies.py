from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..db import system_connection, tenant_connection
from ..security.tokens import decode_access_token
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
) -> Principal:
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
