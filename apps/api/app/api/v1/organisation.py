import ipaddress
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from psycopg.errors import UniqueViolation

from ...db import tenant_connection
from ...schemas.auth import ResponseMeta
from ...schemas.organisation import (
    DepartmentCreateRequest,
    DepartmentData,
    DepartmentListData,
    DepartmentListResponse,
    DepartmentResponse,
    DepartmentUpdateRequest,
    OrganisationData,
    OrganisationResponse,
    OrganisationUpdateRequest,
    TeamCreateRequest,
    TeamData,
    TeamListData,
    TeamListResponse,
    TeamResponse,
    TeamUpdateRequest,
)
from ...security.audit import record_audit
from ...security.permissions import Action, is_allowed
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["organisation"])


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
            "Supply the current resource ETag in the If-Match header.",
        )
    value = if_match.strip()
    prefix = f'"{resource_id}:'
    if not value.startswith(prefix) or not value.endswith('"'):
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this resource.",
        )
    try:
        version = int(value[len(prefix) : -1])
    except ValueError as error:
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this resource.",
        ) from error
    if version < 1:
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this resource.",
        )
    return version


def _require_management(principal: Principal) -> None:
    if not is_allowed(principal.role, Action.MANAGE_ORGANISATION):
        raise ApiError(
            403,
            "permission_denied",
            "Permission denied",
            "Your role cannot manage organisation settings.",
        )


@router.get("/organisation", response_model=OrganisationResponse)
async def get_organisation(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> OrganisationResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        organisation = await (
            await connection.execute(
                """
                SELECT id,name,slug,status,workspace_template_version,default_timezone,
                  version,created_at,updated_at
                FROM organisations WHERE id=%s
                """,
                (principal.organisation_id,),
            )
        ).fetchone()
    response.headers["ETag"] = _etag(organisation["id"], organisation["version"])
    return OrganisationResponse(
        data=OrganisationData.model_validate(organisation),
        meta=_meta(request),
    )


@router.patch("/organisation", response_model=OrganisationResponse)
async def update_organisation(
    payload: OrganisationUpdateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> OrganisationResponse:
    _require_management(principal)
    expected_version = _expected_version(if_match, principal.organisation_id)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        current = await (
            await connection.execute(
                """
                SELECT id,name,slug,status,workspace_template_version,default_timezone,
                  version,created_at,updated_at
                FROM organisations WHERE id=%s FOR UPDATE
                """,
                (principal.organisation_id,),
            )
        ).fetchone()
        if current["version"] != expected_version:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The organisation was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["version"])},
            )
        name = payload.name or current["name"]
        default_timezone = payload.default_timezone or current["default_timezone"]
        if name != current["name"] or default_timezone != current["default_timezone"]:
            organisation = await (
                await connection.execute(
                    """
                    UPDATE organisations
                    SET name=%s,default_timezone=%s,version=version+1,updated_at=now()
                    WHERE id=%s
                    RETURNING id,name,slug,status,workspace_template_version,
                      default_timezone,version,created_at,updated_at
                    """,
                    (name, default_timezone, principal.organisation_id),
                )
            ).fetchone()
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="organisation.updated",
                target_type="organisation",
                target_id=principal.organisation_id,
                before={
                    "name": current["name"],
                    "default_timezone": current["default_timezone"],
                    "version": current["version"],
                },
                after={
                    "name": organisation["name"],
                    "default_timezone": organisation["default_timezone"],
                    "version": organisation["version"],
                },
                ip_address=_client_ip(request),
            )
        else:
            organisation = current
    response.headers["ETag"] = _etag(organisation["id"], organisation["version"])
    return OrganisationResponse(
        data=OrganisationData.model_validate(organisation),
        meta=_meta(request),
    )


@router.get("/departments", response_model=DepartmentListResponse)
async def list_departments(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    department_status: Annotated[
        Literal["active", "archived"] | None,
        Query(alias="status"),
    ] = None,
) -> DepartmentListResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        departments = await (
            await connection.execute(
                """
                SELECT id,organisation_id,name,status,version,created_at,updated_at
                FROM departments
                WHERE (%s::text IS NULL OR status=%s)
                ORDER BY status,name,id
                """,
                (department_status, department_status),
            )
        ).fetchall()
    return DepartmentListResponse(
        data=DepartmentListData(
            items=[DepartmentData.model_validate(department) for department in departments]
        ),
        meta=_meta(request),
    )


@router.post(
    "/departments",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_department(
    payload: DepartmentCreateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> DepartmentResponse:
    _require_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        department = await (
            await connection.execute(
                """
                INSERT INTO departments(organisation_id,name)
                VALUES (%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING id,organisation_id,name,status,version,created_at,updated_at
                """,
                (principal.organisation_id, payload.name),
            )
        ).fetchone()
        if not department:
            raise ApiError(
                409,
                "department_name_conflict",
                "Department already exists",
                "A department with that name already exists in this organisation.",
            )
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="department.created",
            target_type="department",
            target_id=department["id"],
            after={
                "name": department["name"],
                "status": department["status"],
                "version": department["version"],
            },
            ip_address=_client_ip(request),
        )
    response.headers["ETag"] = _etag(department["id"], department["version"])
    return DepartmentResponse(
        data=DepartmentData.model_validate(department),
        meta=_meta(request),
    )


@router.patch("/departments/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: UUID,
    payload: DepartmentUpdateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> DepartmentResponse:
    _require_management(principal)
    expected_version = _expected_version(if_match, department_id)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        current = await (
            await connection.execute(
                """
                SELECT id,organisation_id,name,status,version,created_at,updated_at
                FROM departments WHERE id=%s FOR UPDATE
                """,
                (department_id,),
            )
        ).fetchone()
        if not current:
            raise ApiError(
                404,
                "department_not_found",
                "Department not found",
                "The requested department does not exist.",
            )
        if current["version"] != expected_version:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The department was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["version"])},
            )
        name = payload.name or current["name"]
        new_status = payload.status or current["status"]
        if new_status == "archived" and current["status"] != "archived":
            usage = await (
                await connection.execute(
                    """
                    SELECT
                      (SELECT count(*) FROM user_profiles WHERE primary_department_id=%s) members,
                      (SELECT count(*) FROM teams WHERE department_id=%s AND status='active') teams,
                      (SELECT count(*) FROM sites
                       WHERE managing_department_id=%s AND status<>'deleted') sites
                    """,
                    (department_id, department_id, department_id),
                )
            ).fetchone()
            if any(usage.values()):
                raise ApiError(
                    409,
                    "department_in_use",
                    "Department is in use",
                    "Move its members, archive its teams, and reassign its sites before archiving it.",
                )
        duplicate = await (
            await connection.execute(
                """
                SELECT 1 FROM departments
                WHERE id<>%s AND lower(name)=lower(%s)
                """,
                (department_id, name),
            )
        ).fetchone()
        if duplicate:
            raise ApiError(
                409,
                "department_name_conflict",
                "Department already exists",
                "A department with that name already exists in this organisation.",
            )
        if name != current["name"] or new_status != current["status"]:
            try:
                async with connection.transaction():
                    department = await (
                        await connection.execute(
                            """
                            UPDATE departments
                            SET name=%s,status=%s,version=version+1,updated_at=now()
                            WHERE id=%s
                            RETURNING id,organisation_id,name,status,version,
                              created_at,updated_at
                            """,
                            (name, new_status, department_id),
                        )
                    ).fetchone()
            except UniqueViolation as error:
                raise ApiError(
                    409,
                    "department_name_conflict",
                    "Department already exists",
                    "A department with that name already exists in this organisation.",
                ) from error
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="department.updated",
                target_type="department",
                target_id=department_id,
                before={
                    "name": current["name"],
                    "status": current["status"],
                    "version": current["version"],
                },
                after={
                    "name": department["name"],
                    "status": department["status"],
                    "version": department["version"],
                },
                ip_address=_client_ip(request),
            )
        else:
            department = current
    response.headers["ETag"] = _etag(department["id"], department["version"])
    return DepartmentResponse(
        data=DepartmentData.model_validate(department),
        meta=_meta(request),
    )


@router.get("/teams", response_model=TeamListResponse)
async def list_teams(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    department_id: Annotated[UUID | None, Query()] = None,
    team_status: Annotated[
        Literal["active", "archived"] | None,
        Query(alias="status"),
    ] = None,
) -> TeamListResponse:
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        teams = await (
            await connection.execute(
                """
                SELECT t.id,t.organisation_id,t.department_id,d.name department_name,
                  t.name,t.status,t.version,t.created_at,t.updated_at
                FROM teams t
                JOIN departments d ON d.id=t.department_id
                WHERE (%s::uuid IS NULL OR t.department_id=%s)
                  AND (%s::text IS NULL OR t.status=%s)
                ORDER BY d.name,t.status,t.name,t.id
                """,
                (department_id, department_id, team_status, team_status),
            )
        ).fetchall()
    return TeamListResponse(
        data=TeamListData(items=[TeamData.model_validate(team) for team in teams]),
        meta=_meta(request),
    )


@router.post("/teams", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> TeamResponse:
    _require_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        department = await (
            await connection.execute(
                "SELECT id,name,status FROM departments WHERE id=%s",
                (payload.department_id,),
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
                "Teams can only be created in an active department.",
            )
        team = await (
            await connection.execute(
                """
                INSERT INTO teams(organisation_id,department_id,name)
                VALUES (%s,%s,%s)
                ON CONFLICT DO NOTHING
                RETURNING id,organisation_id,department_id,name,status,version,created_at,updated_at
                """,
                (principal.organisation_id, payload.department_id, payload.name),
            )
        ).fetchone()
        if not team:
            raise ApiError(
                409,
                "team_name_conflict",
                "Team already exists",
                "A team with that name already exists in this department.",
            )
        team["department_name"] = department["name"]
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="team.created",
            target_type="team",
            target_id=team["id"],
            after={
                "department_id": str(team["department_id"]),
                "name": team["name"],
                "status": team["status"],
                "version": team["version"],
            },
            ip_address=_client_ip(request),
        )
    response.headers["ETag"] = _etag(team["id"], team["version"])
    return TeamResponse(data=TeamData.model_validate(team), meta=_meta(request))


@router.patch("/teams/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: UUID,
    payload: TeamUpdateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> TeamResponse:
    _require_management(principal)
    expected_version = _expected_version(if_match, team_id)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        current = await (
            await connection.execute(
                """
                SELECT t.id,t.organisation_id,t.department_id,d.name department_name,
                  d.status department_status,t.name,t.status,t.version,t.created_at,t.updated_at
                FROM teams t JOIN departments d ON d.id=t.department_id
                WHERE t.id=%s FOR UPDATE OF t
                """,
                (team_id,),
            )
        ).fetchone()
        if not current:
            raise ApiError(
                404,
                "team_not_found",
                "Team not found",
                "The requested team does not exist.",
            )
        if current["version"] != expected_version:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The team was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["version"])},
            )
        name = payload.name or current["name"]
        new_status = payload.status or current["status"]
        if new_status == "active" and current["department_status"] != "active":
            raise ApiError(
                409,
                "department_archived",
                "Department is archived",
                "A team cannot be activated inside an archived department.",
            )
        duplicate = await (
            await connection.execute(
                """
                SELECT 1 FROM teams
                WHERE id<>%s AND department_id=%s AND lower(name)=lower(%s)
                """,
                (team_id, current["department_id"], name),
            )
        ).fetchone()
        if duplicate:
            raise ApiError(
                409,
                "team_name_conflict",
                "Team already exists",
                "A team with that name already exists in this department.",
            )
        if name != current["name"] or new_status != current["status"]:
            try:
                async with connection.transaction():
                    team = await (
                        await connection.execute(
                            """
                            UPDATE teams
                            SET name=%s,status=%s,version=version+1,updated_at=now()
                            WHERE id=%s
                            RETURNING id,organisation_id,department_id,name,status,
                              version,created_at,updated_at
                            """,
                            (name, new_status, team_id),
                        )
                    ).fetchone()
            except UniqueViolation as error:
                raise ApiError(
                    409,
                    "team_name_conflict",
                    "Team already exists",
                    "A team with that name already exists in this department.",
                ) from error
            team["department_name"] = current["department_name"]
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="team.updated",
                target_type="team",
                target_id=team_id,
                before={
                    "name": current["name"],
                    "status": current["status"],
                    "version": current["version"],
                },
                after={
                    "name": team["name"],
                    "status": team["status"],
                    "version": team["version"],
                },
                ip_address=_client_ip(request),
            )
        else:
            team = current
    response.headers["ETag"] = _etag(team["id"], team["version"])
    return TeamResponse(data=TeamData.model_validate(team), meta=_meta(request))
