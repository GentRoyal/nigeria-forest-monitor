import ipaddress
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from ...db import tenant_connection
from ...schemas.auth import EmptyData, EmptyResponse, ResponseMeta
from ...schemas.members import (
    MemberData,
    MemberListData,
    MemberListResponse,
    MemberResponse,
    MemberTeamData,
    MemberUpdateRequest,
    TeamMembershipData,
    TeamMembershipResponse,
)
from ...security.audit import record_audit
from ...security.permissions import Action, Role, is_allowed
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["members"])


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=UUID(request.state.request_id))


def _client_ip(request: Request) -> str | None:
    if not request.client:
        return None
    try:
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


def _etag(resource_id: UUID, version: int) -> str:
    return f'"{resource_id}:{version}"'


def _expected_version(if_match: str | None, resource_id: UUID) -> int:
    if if_match is None:
        raise ApiError(
            428,
            "precondition_required",
            "Update precondition required",
            "Supply the current member ETag in the If-Match header.",
        )
    value = if_match.strip()
    prefix = f'"{resource_id}:'
    if not value.startswith(prefix) or not value.endswith('"'):
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this member.",
        )
    try:
        version = int(value[len(prefix) : -1])
    except ValueError as error:
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this member.",
        ) from error
    if version < 1:
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this member.",
        )
    return version


def _require_member_management(principal: Principal) -> None:
    if not is_allowed(principal.role, Action.MANAGE_MEMBERS):
        raise ApiError(
            403,
            "permission_denied",
            "Permission denied",
            "Your role cannot view or manage organisation members.",
        )


def _member_data(row: dict) -> MemberData:
    return MemberData(
        id=row["id"],
        organisation_id=row["organisation_id"],
        department_id=row["primary_department_id"],
        department_name=row["department_name"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        status=row["status"],
        timezone=row["timezone"],
        teams=[MemberTeamData.model_validate(team) for team in row["teams"]],
        version=row["version"],
        invited_at=row["invited_at"],
        activated_at=row["activated_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _load_member(connection, user_id: UUID, *, for_update: bool = False) -> dict | None:
    lock = "FOR UPDATE OF u" if for_update else ""
    return await (
        await connection.execute(
            f"""
            SELECT u.id,u.organisation_id,u.primary_department_id,u.email::text email,
              u.display_name,u.role,u.status,u.timezone,u.version,u.invited_at,
              u.activated_at,u.created_at,u.updated_at,d.name department_name,
              COALESCE((
                SELECT jsonb_agg(
                  jsonb_build_object('id',t.id::text,'name',t.name)
                  ORDER BY t.name,t.id
                )
                FROM team_memberships tm
                JOIN teams t ON t.id=tm.team_id
                WHERE tm.user_id=u.id AND tm.status='active' AND t.status='active'
              ),'[]'::jsonb) teams
            FROM user_profiles u
            JOIN departments d ON d.id=u.primary_department_id
            WHERE u.id=%s
            {lock}
            """,
            (user_id,),
        )
    ).fetchone()


@router.get("/members", response_model=MemberListResponse)
async def list_members(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    query: Annotated[str | None, Query(alias="q", min_length=1, max_length=160)] = None,
    role: Annotated[
        Literal["owner", "administrator", "analyst", "verification_officer", "viewer"]
        | None,
        Query(),
    ] = None,
    member_status: Annotated[
        Literal["invited", "active", "suspended", "disabled", "expired"] | None,
        Query(alias="status"),
    ] = None,
    department_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MemberListResponse:
    _require_member_management(principal)
    search = f"%{query.strip()}%" if query else None
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        total = await (
            await connection.execute(
                """
                SELECT count(*) count FROM user_profiles
                WHERE (%s::text IS NULL OR email ILIKE %s OR display_name ILIKE %s)
                  AND (%s::text IS NULL OR role=%s)
                  AND (%s::text IS NULL OR status=%s)
                  AND (%s::uuid IS NULL OR primary_department_id=%s)
                """,
                (
                    search,
                    search,
                    search,
                    role,
                    role,
                    member_status,
                    member_status,
                    department_id,
                    department_id,
                ),
            )
        ).fetchone()
        members = await (
            await connection.execute(
                """
                SELECT u.id,u.organisation_id,u.primary_department_id,u.email::text email,
                  u.display_name,u.role,u.status,u.timezone,u.version,u.invited_at,
                  u.activated_at,u.created_at,u.updated_at,d.name department_name,
                  COALESCE((
                    SELECT jsonb_agg(
                      jsonb_build_object('id',t.id::text,'name',t.name)
                      ORDER BY t.name,t.id
                    )
                    FROM team_memberships tm
                    JOIN teams t ON t.id=tm.team_id
                    WHERE tm.user_id=u.id AND tm.status='active' AND t.status='active'
                  ),'[]'::jsonb) teams
                FROM user_profiles u
                JOIN departments d ON d.id=u.primary_department_id
                WHERE (%s::text IS NULL OR u.email ILIKE %s OR u.display_name ILIKE %s)
                  AND (%s::text IS NULL OR u.role=%s)
                  AND (%s::text IS NULL OR u.status=%s)
                  AND (%s::uuid IS NULL OR u.primary_department_id=%s)
                ORDER BY u.display_name,u.id LIMIT %s OFFSET %s
                """,
                (
                    search,
                    search,
                    search,
                    role,
                    role,
                    member_status,
                    member_status,
                    department_id,
                    department_id,
                    limit,
                    offset,
                ),
            )
        ).fetchall()
    return MemberListResponse(
        data=MemberListData(
            items=[_member_data(member) for member in members],
            total=total["count"],
            limit=limit,
            offset=offset,
        ),
        meta=_meta(request),
    )


@router.get("/members/{user_id}", response_model=MemberResponse)
async def get_member(
    user_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> MemberResponse:
    _require_member_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        member = await _load_member(connection, user_id)
    if not member:
        raise ApiError(404, "member_not_found", "Member not found", "The member does not exist.")
    response.headers["ETag"] = _etag(member["id"], member["version"])
    return MemberResponse(data=_member_data(member), meta=_meta(request))


@router.patch("/members/{user_id}", response_model=MemberResponse)
async def update_member(
    user_id: UUID,
    payload: MemberUpdateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> MemberResponse:
    _require_member_management(principal)
    expected_version = _expected_version(if_match, user_id)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        current = await _load_member(connection, user_id, for_update=True)
        if not current:
            raise ApiError(404, "member_not_found", "Member not found", "The member does not exist.")
        if current["version"] != expected_version:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The member was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["version"])},
            )
        new_role = payload.role or current["role"]
        new_status = payload.status or current["status"]
        department_id = payload.department_id or current["primary_department_id"]
        if principal.role != Role.OWNER and (
            current["role"] == Role.OWNER or new_role == Role.OWNER
        ):
            raise ApiError(
                403,
                "owner_role_protected",
                "Owner role protected",
                "Only an owner can grant or modify the owner role.",
            )
        if current["status"] in {"invited", "expired"}:
            raise ApiError(
                409,
                "member_not_activated",
                "Member is not activated",
                "Pending accounts must be activated through invitation acceptance.",
            )
        if current["role"] == Role.OWNER and (
            new_role != Role.OWNER or new_status != "active"
        ):
            owners = await (
                await connection.execute(
                    """
                    SELECT count(*) count FROM user_profiles
                    WHERE role='owner' AND status='active'
                    """
                )
            ).fetchone()
            if owners["count"] <= 1:
                raise ApiError(
                    409,
                    "last_owner_protected",
                    "Last owner protected",
                    "The last active owner cannot be disabled, suspended, or demoted.",
                )
        department = await (
            await connection.execute(
                "SELECT id,name,status FROM departments WHERE id=%s",
                (department_id,),
            )
        ).fetchone()
        if not department:
            raise ApiError(
                404,
                "department_not_found",
                "Department not found",
                "The requested department does not exist.",
            )
        if department["status"] != "active":
            raise ApiError(
                409,
                "department_archived",
                "Department is archived",
                "Members can only belong to an active department.",
            )
        changed = (
            new_role != current["role"]
            or new_status != current["status"]
            or department_id != current["primary_department_id"]
        )
        if changed:
            if department_id != current["primary_department_id"] or new_status != "active":
                await connection.execute(
                    """
                    UPDATE team_memberships SET status='inactive'
                    WHERE user_id=%s AND status='active'
                    """,
                    (user_id,),
                )
            await connection.execute(
                """
                UPDATE user_profiles
                SET role=%s,status=%s,primary_department_id=%s,
                  version=version+1,updated_at=now()
                WHERE id=%s
                """,
                (new_role, new_status, department_id, user_id),
            )
            await connection.execute(
                """
                UPDATE auth_sessions SET revoked_at=now(),revoke_reason='member access changed'
                WHERE user_id=%s AND revoked_at IS NULL
                """,
                (user_id,),
            )
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="membership.updated",
                target_type="user_profile",
                target_id=user_id,
                before={
                    "role": current["role"],
                    "status": current["status"],
                    "department_id": str(current["primary_department_id"]),
                    "version": current["version"],
                },
                after={
                    "role": new_role,
                    "status": new_status,
                    "department_id": str(department_id),
                    "version": current["version"] + 1,
                },
                reason=payload.reason,
                ip_address=_client_ip(request),
            )
        member = await _load_member(connection, user_id)
    response.headers["ETag"] = _etag(member["id"], member["version"])
    return MemberResponse(data=_member_data(member), meta=_meta(request))


@router.put("/teams/{team_id}/members/{user_id}", response_model=TeamMembershipResponse)
async def add_team_member(
    team_id: UUID,
    user_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> TeamMembershipResponse:
    _require_member_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        team = await (
            await connection.execute(
                "SELECT id,department_id,status FROM teams WHERE id=%s",
                (team_id,),
            )
        ).fetchone()
        member = await (
            await connection.execute(
                "SELECT id,primary_department_id,status FROM user_profiles WHERE id=%s",
                (user_id,),
            )
        ).fetchone()
        if not team:
            raise ApiError(404, "team_not_found", "Team not found", "The team does not exist.")
        if not member:
            raise ApiError(404, "member_not_found", "Member not found", "The member does not exist.")
        if team["status"] != "active" or member["status"] != "active":
            raise ApiError(
                409,
                "inactive_membership_resource",
                "Membership cannot be activated",
                "Both the team and member must be active.",
            )
        if team["department_id"] != member["primary_department_id"]:
            raise ApiError(
                409,
                "membership_department_mismatch",
                "Department mismatch",
                "A member can only join teams in their primary department.",
            )
        previous = await (
            await connection.execute(
                "SELECT id,status FROM team_memberships WHERE team_id=%s AND user_id=%s",
                (team_id, user_id),
            )
        ).fetchone()
        membership = await (
            await connection.execute(
                """
                INSERT INTO team_memberships(organisation_id,team_id,user_id,created_by)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (organisation_id,team_id,user_id)
                DO UPDATE SET status='active'
                RETURNING id,team_id,user_id,status,created_at
                """,
                (principal.organisation_id, team_id, user_id, principal.user_id),
            )
        ).fetchone()
        if not previous or previous["status"] != "active":
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="team.membership_added",
                target_type="team_membership",
                target_id=membership["id"],
                after={"team_id": str(team_id), "user_id": str(user_id), "status": "active"},
                ip_address=_client_ip(request),
            )
    return TeamMembershipResponse(
        data=TeamMembershipData.model_validate(membership),
        meta=_meta(request),
    )


@router.delete("/teams/{team_id}/members/{user_id}", response_model=EmptyResponse)
async def remove_team_member(
    team_id: UUID,
    user_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> EmptyResponse:
    _require_member_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        membership = await (
            await connection.execute(
                """
                UPDATE team_memberships SET status='inactive'
                WHERE team_id=%s AND user_id=%s AND status='active'
                RETURNING id
                """,
                (team_id, user_id),
            )
        ).fetchone()
        if membership:
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="team.membership_removed",
                target_type="team_membership",
                target_id=membership["id"],
                before={"team_id": str(team_id), "user_id": str(user_id), "status": "active"},
                after={"team_id": str(team_id), "user_id": str(user_id), "status": "inactive"},
                ip_address=_client_ip(request),
            )
    return EmptyResponse(data=EmptyData(), meta=_meta(request))
