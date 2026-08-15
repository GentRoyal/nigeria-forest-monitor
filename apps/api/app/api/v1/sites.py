import ipaddress
import json
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.auth import ResponseMeta
from ...schemas.sites import (
    BoundaryData,
    BoundaryListMeta,
    BoundaryListResponse,
    BoundaryResponse,
    BoundaryVersionCreateRequest,
    GridCellData,
    GridCellListMeta,
    GridCellListResponse,
    GridGenerateRequest,
    GridVersionData,
    GridVersionListMeta,
    GridVersionListResponse,
    GridVersionResponse,
    ScheduleData,
    ScheduleResponse,
    ScheduleSuspendRequest,
    ScheduleUpsertRequest,
    SiteCreateRequest,
    SiteData,
    SiteListMeta,
    SiteListResponse,
    SiteResponse,
    SiteUpdateRequest,
    TagData,
)
from ...security.audit import record_audit
from ...security.cursors import (
    CursorError,
    CursorPosition,
    cursor_scope,
    decode_cursor,
    encode_cursor,
)
from ...security.permissions import Action, Role, is_allowed
from ...settings import get_settings
from ...spatial import AoiValidationError, validate_aoi
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["sites"])


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


def _next_due_at(cadence: str, now: datetime) -> datetime:
    if cadence == "weekly":
        return now + timedelta(days=7)
    if cadence == "fortnightly":
        return now + timedelta(days=14)
    year = now.year + (now.month == 12)
    month = 1 if now.month == 12 else now.month + 1
    return now.replace(year=year, month=month, day=min(now.day, monthrange(year, month)[1]))


def _expected_version(value: str | None, resource_id: UUID) -> int:
    if value is None:
        raise ApiError(
            428,
            "precondition_required",
            "Update precondition required",
            "Supply the current site ETag in the If-Match header.",
        )
    prefix = f'"{resource_id}:'
    if not value.startswith(prefix) or not value.endswith('"'):
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this site.",
        )
    try:
        version = int(value[len(prefix) : -1])
    except ValueError as error:
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this site.",
        ) from error
    if version < 1:
        raise ApiError(
            400,
            "invalid_if_match",
            "Invalid update precondition",
            "The If-Match header is not a valid ETag for this site.",
        )
    return version


def _require_management(principal: Principal) -> None:
    if not is_allowed(principal.role, Action.MANAGE_SITES):
        raise ApiError(
            403,
            "permission_denied",
            "Permission denied",
            "Your role cannot create or update sites.",
        )


def _visibility_sql(principal: Principal, alias: str = "s") -> tuple[str, list[Any]]:
    if principal.role in {Role.OWNER, Role.ADMINISTRATOR}:
        return "TRUE", []
    if principal.role == Role.ANALYST:
        return (
            f"""({alias}.sensitivity='normal' OR EXISTS (
          SELECT 1 FROM site_team_access sta
          JOIN team_memberships tm ON tm.team_id=sta.team_id AND tm.status='active'
          JOIN teams t ON t.id=sta.team_id AND t.status='active'
          WHERE sta.site_id={alias}.id AND tm.user_id=%s
        ))""",
            [principal.user_id],
        )
    if principal.role == Role.VERIFICATION_OFFICER:
        return (
            f"""EXISTS (
          SELECT 1 FROM change_events ce
          JOIN event_assignments ea ON ea.event_id=ce.id
          WHERE ce.site_id={alias}.id AND ea.assignee_id=%s
            AND ea.assignment_type='institutional_verification'
            AND ea.status IN ('pending','accepted')
        )""",
            [principal.user_id],
        )
    return "FALSE", []


_SITE_COLUMNS = """
  s.id,s.organisation_id,s.managing_department_id,d.name managing_department_name,
  s.name,s.slug,s.description,s.origin,s.sensitivity,s.status,s.monitoring_health,
  s.version,s.current_grid_version_id,s.created_at,s.updated_at,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('id',t.id,'name',t.name) ORDER BY t.name)
    FROM site_tags st JOIN tags t ON t.id=st.tag_id WHERE st.site_id=s.id),'[]'::jsonb) tags,
  b.id boundary_id,b.version boundary_version,
  CASE WHEN b.id IS NULL THEN NULL ELSE ST_AsGeoJSON(b.geometry,9,0)::jsonb END boundary_geometry,
  b.source_authority,b.source_identifier,b.source_url,b.licence,b.attribution,
  b.effective_date,b.source_crs,b.checksum,b.validation_result,b.created_at boundary_created_at,
  b.created_by boundary_created_by,b.change_reason boundary_change_reason,
  b.superseded_at boundary_superseded_at,
  (b.id=s.current_boundary_version_id) boundary_is_current,
  CASE WHEN b.id IS NULL THEN NULL ELSE ST_Area(b.geometry::geography)/1000000.0 END area_sq_km,
  CASE WHEN b.id IS NULL THEN NULL ELSE jsonb_build_object(
    'west',ST_XMin(Box2D(b.geometry)),'south',ST_YMin(Box2D(b.geometry)),
    'east',ST_XMax(Box2D(b.geometry)),'north',ST_YMax(Box2D(b.geometry))) END bounds
"""

_BOUNDARY_COLUMNS = """
  b.id boundary_id,b.version boundary_version,
  CASE WHEN %s::boolean THEN ST_AsGeoJSON(b.geometry,9,0)::jsonb ELSE NULL END boundary_geometry,
  b.source_authority,b.source_identifier,b.source_url,b.licence,b.attribution,
  b.effective_date,b.source_crs,b.checksum,b.validation_result,b.created_at boundary_created_at,
  b.created_by boundary_created_by,b.change_reason boundary_change_reason,
  b.superseded_at boundary_superseded_at,
  (b.id=s.current_boundary_version_id) boundary_is_current,
  ST_Area(b.geometry::geography)/1000000.0 area_sq_km,
  jsonb_build_object(
    'west',ST_XMin(Box2D(b.geometry)),'south',ST_YMin(Box2D(b.geometry)),
    'east',ST_XMax(Box2D(b.geometry)),'north',ST_YMax(Box2D(b.geometry))) bounds
"""


def _boundary_data(row: dict[str, Any], *, include_geometry: bool) -> BoundaryData | None:
    if not row["boundary_id"]:
        return None
    return BoundaryData(
        id=row["boundary_id"],
        version=row["boundary_version"],
        geometry=row["boundary_geometry"] if include_geometry else None,
        source_authority=row["source_authority"],
        source_identifier=row["source_identifier"],
        source_url=row["source_url"],
        licence=row["licence"],
        attribution=row["attribution"],
        effective_date=row["effective_date"],
        source_crs=row["source_crs"],
        checksum=row["checksum"],
        validation_result=row["validation_result"],
        area_sq_km=float(row["area_sq_km"]),
        bounds=row["bounds"],
        created_by=row["boundary_created_by"],
        change_reason=row["boundary_change_reason"],
        superseded_at=row["boundary_superseded_at"],
        is_current=row["boundary_is_current"],
        created_at=row["boundary_created_at"],
    )


def _site_data(row: dict[str, Any], *, include_geometry: bool) -> SiteData:
    boundary = _boundary_data(row, include_geometry=include_geometry)
    return SiteData(
        id=row["id"],
        organisation_id=row["organisation_id"],
        managing_department_id=row["managing_department_id"],
        managing_department_name=row["managing_department_name"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        origin=row["origin"],
        sensitivity=row["sensitivity"],
        status=row["status"],
        monitoring_health=row["monitoring_health"],
        version=row["version"],
        tags=[TagData.model_validate(tag) for tag in row["tags"]],
        current_boundary=boundary,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _load_site(
    connection, site_id: UUID, visibility: str, visibility_params: list[Any], *, lock: bool = False
) -> dict[str, Any] | None:
    lock_sql = "FOR UPDATE OF s" if lock else ""
    return await (
        await connection.execute(
            f"""SELECT {_SITE_COLUMNS} FROM sites s
        JOIN departments d ON d.id=s.managing_department_id
        LEFT JOIN site_boundary_versions b ON b.id=s.current_boundary_version_id
        WHERE s.id=%s AND s.status<>'deleted' AND ({visibility}) {lock_sql}""",
            [site_id, *visibility_params],
        )
    ).fetchone()


def _bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        parts = [float(part.strip()) for part in value.split(",")]
    except ValueError as error:
        raise ApiError(
            422,
            "invalid_bbox",
            "Invalid bounding box",
            "bbox must contain west,south,east,north numbers.",
        ) from error
    if len(parts) != 4:
        raise ApiError(
            422,
            "invalid_bbox",
            "Invalid bounding box",
            "bbox must contain west,south,east,north numbers.",
        )
    west, south, east, north = parts
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ApiError(
            422,
            "invalid_bbox",
            "Invalid bounding box",
            "bbox coordinates are out of range or not ordered.",
        )
    return west, south, east, north


@router.get("/sites", response_model=SiteListResponse)
async def list_sites(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    q: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    status: Annotated[Literal["active", "archived"] | None, Query()] = None,
    sensitivity: Annotated[Literal["normal", "sensitive"] | None, Query()] = None,
    managing_department_id: Annotated[UUID | None, Query()] = None,
    tag: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    bbox: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> SiteListResponse:
    bounds = _bbox(bbox)
    query = q.strip() if q else None
    tag = tag.strip().lower() if tag else None
    scope = cursor_scope(
        {
            "org": str(principal.organisation_id),
            "role": principal.role,
            "user": str(principal.user_id),
            "q": query,
            "status": status,
            "sensitivity": sensitivity,
            "department": managing_department_id,
            "tag": tag,
            "bbox": bounds,
        }
    )
    position = None
    if cursor:
        try:
            position = decode_cursor(cursor, scope=scope)
        except CursorError as error:
            raise ApiError(
                400,
                "invalid_cursor",
                "Invalid pagination cursor",
                "The cursor is invalid or does not belong to this query.",
            ) from error
    visibility, visibility_params = _visibility_sql(principal)
    search = f"%{query}%" if query else None
    cursor_time = position.created_at if position else None
    cursor_id = position.resource_id if position else None
    bbox_values = bounds or (None, None, None, None)
    params: list[Any] = [
        *visibility_params,
        search,
        search,
        search,
        status,
        status,
        sensitivity,
        sensitivity,
        managing_department_id,
        managing_department_id,
        tag,
        tag,
        bbox_values[0],
        *bbox_values,
        *bbox_values,
        cursor_time,
        cursor_time,
        cursor_id,
        limit + 1,
    ]
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        rows = await (
            await connection.execute(
                f"""SELECT {_SITE_COLUMNS} FROM sites s
            JOIN departments d ON d.id=s.managing_department_id
            LEFT JOIN site_boundary_versions b ON b.id=s.current_boundary_version_id
            WHERE s.status<>'deleted' AND ({visibility})
              AND (%s::text IS NULL OR s.name ILIKE %s OR s.slug ILIKE %s)
              AND (%s::text IS NULL OR s.status=%s)
              AND (%s::text IS NULL OR s.sensitivity=%s)
              AND (%s::uuid IS NULL OR s.managing_department_id=%s)
              AND (%s::text IS NULL OR EXISTS (SELECT 1 FROM site_tags st2 JOIN tags t2 ON t2.id=st2.tag_id WHERE st2.site_id=s.id AND t2.name=%s))
              AND (%s::double precision IS NULL OR (b.geometry && ST_MakeEnvelope(%s,%s,%s,%s,4326) AND ST_Intersects(b.geometry,ST_MakeEnvelope(%s,%s,%s,%s,4326))))
              AND (%s::timestamptz IS NULL OR (s.created_at,s.id)<(%s,%s::uuid))
            ORDER BY s.created_at DESC,s.id DESC LIMIT %s""",
                params,
            )
        ).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = (
        encode_cursor(CursorPosition(page[-1]["created_at"], page[-1]["id"]), scope=scope)
        if has_more
        else None
    )
    return SiteListResponse(
        data=[_site_data(row, include_geometry=False) for row in page],
        meta=SiteListMeta(request_id=UUID(request.state.request_id), next_cursor=next_cursor),
    )


@router.post("/sites", response_model=SiteResponse, status_code=201)
async def create_site(
    payload: SiteCreateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> SiteResponse:
    _require_management(principal)
    provenance = payload.boundary.model_dump(exclude={"geometry"}, mode="json")
    settings = get_settings()
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        department = await (
            await connection.execute(
                "SELECT id,status FROM departments WHERE id=%s", (payload.managing_department_id,)
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
                "A site must have an active managing department.",
            )
        try:
            aoi = await validate_aoi(
                connection,
                geometry=payload.boundary.geometry.model_dump(),
                source_crs=payload.boundary.source_crs,
                provenance=provenance,
                max_area_sq_km=settings.max_aoi_area_sq_km,
                max_vertices=settings.max_aoi_vertices,
            )
        except AoiValidationError as error:
            raise ApiError(422, "invalid_geometry", "Invalid site boundary", str(error)) from error
        try:
            site = await (
                await connection.execute(
                    """INSERT INTO sites(organisation_id,managing_department_id,name,slug,description,origin,sensitivity,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (
                        principal.organisation_id,
                        payload.managing_department_id,
                        payload.name,
                        payload.slug,
                        payload.description,
                        payload.origin,
                        payload.sensitivity,
                        principal.user_id,
                    ),
                )
            ).fetchone()
            boundary = await (
                await connection.execute(
                    """INSERT INTO site_boundary_versions(organisation_id,site_id,version,geometry,source_authority,source_identifier,source_url,licence,attribution,effective_date,source_crs,validation_result,checksum,created_by,change_reason)
                VALUES (%s,%s,1,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'Initial boundary supplied during site creation') RETURNING id""",
                    (
                        principal.organisation_id,
                        site["id"],
                        json.dumps(aoi.geometry),
                        payload.boundary.source_authority,
                        payload.boundary.source_identifier,
                        payload.boundary.source_url,
                        payload.boundary.licence,
                        payload.boundary.attribution,
                        payload.boundary.effective_date,
                        payload.boundary.source_crs,
                        Jsonb(aoi.validation_result),
                        aoi.checksum,
                        principal.user_id,
                    ),
                )
            ).fetchone()
            await connection.execute(
                "UPDATE sites SET current_boundary_version_id=%s WHERE id=%s",
                (boundary["id"], site["id"]),
            )
            for name in payload.tags:
                tag_row = await (
                    await connection.execute(
                        "INSERT INTO tags(organisation_id,name,created_by) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                        (principal.organisation_id, name, principal.user_id),
                    )
                ).fetchone()
                tag_id = (
                    tag_row["id"]
                    if tag_row
                    else (
                        await (
                            await connection.execute(
                                "SELECT id FROM tags WHERE lower(name)=lower(%s)", (name,)
                            )
                        ).fetchone()
                    )["id"]
                )
                await connection.execute(
                    "INSERT INTO site_tags(organisation_id,site_id,tag_id,attached_by) VALUES (%s,%s,%s,%s)",
                    (principal.organisation_id, site["id"], tag_id, principal.user_id),
                )
        except UniqueViolation as error:
            raise ApiError(
                409,
                "site_slug_conflict",
                "Site slug already exists",
                "Another site already uses this slug.",
            ) from error
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="site.created",
            target_type="site",
            target_id=site["id"],
            after={
                "name": payload.name,
                "slug": payload.slug,
                "origin": payload.origin,
                "sensitivity": payload.sensitivity,
                "managing_department_id": str(payload.managing_department_id),
                "boundary_checksum": aoi.checksum,
            },
            ip_address=_client_ip(request),
        )
        visibility, visibility_params = _visibility_sql(principal)
        created = await _load_site(connection, site["id"], visibility, visibility_params)
    response.headers["ETag"] = _etag(created["id"], created["version"])
    response.headers["Location"] = f"/api/v1/sites/{created['id']}"
    return SiteResponse(data=_site_data(created, include_geometry=True), meta=_meta(request))


@router.get("/sites/{site_id}/boundaries", response_model=BoundaryListResponse)
async def list_site_boundaries(
    site_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    include_geometry: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> BoundaryListResponse:
    visibility, visibility_params = _visibility_sql(principal)
    scope = cursor_scope(
        {
            "organisation_id": str(principal.organisation_id),
            "user_id": str(principal.user_id),
            "role": principal.role,
            "site_id": str(site_id),
            "include_geometry": include_geometry,
        }
    )
    position = None
    if cursor:
        try:
            position = decode_cursor(cursor, scope=scope)
        except CursorError as error:
            raise ApiError(
                400,
                "invalid_cursor",
                "Invalid pagination cursor",
                "The cursor is invalid or does not belong to this boundary query.",
            ) from error
    cursor_time = position.created_at if position else None
    cursor_id = position.resource_id if position else None
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        rows = await (
            await connection.execute(
                f"""SELECT {_BOUNDARY_COLUMNS}
                FROM site_boundary_versions b
                JOIN sites s ON s.id=b.site_id
                WHERE b.site_id=%s
                  AND (%s::timestamptz IS NULL OR (b.created_at,b.id)<(%s,%s::uuid))
                ORDER BY b.created_at DESC,b.id DESC LIMIT %s""",
                (include_geometry, site_id, cursor_time, cursor_time, cursor_id, limit + 1),
            )
        ).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = (
        encode_cursor(
            CursorPosition(page[-1]["boundary_created_at"], page[-1]["boundary_id"]),
            scope=scope,
        )
        if has_more
        else None
    )
    return BoundaryListResponse(
        data=[_boundary_data(row, include_geometry=include_geometry) for row in page],
        meta=BoundaryListMeta(request_id=UUID(request.state.request_id), next_cursor=next_cursor),
    )


@router.post("/sites/{site_id}/boundaries", response_model=BoundaryResponse, status_code=201)
async def create_site_boundary(
    site_id: UUID,
    payload: BoundaryVersionCreateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> BoundaryResponse:
    _require_management(principal)
    expected = _expected_version(if_match, site_id)
    visibility, visibility_params = _visibility_sql(principal)
    provenance = payload.model_dump(exclude={"geometry", "reason"}, mode="json")
    settings = get_settings()
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params, lock=True)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        if site["version"] != expected:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The site was changed after it was loaded.",
                {"ETag": _etag(site["id"], site["version"])},
            )
        try:
            aoi = await validate_aoi(
                connection,
                geometry=payload.geometry.model_dump(),
                source_crs=payload.source_crs,
                provenance=provenance,
                max_area_sq_km=settings.max_aoi_area_sq_km,
                max_vertices=settings.max_aoi_vertices,
            )
        except AoiValidationError as error:
            raise ApiError(422, "invalid_geometry", "Invalid site boundary", str(error)) from error
        if site["boundary_id"]:
            unchanged = await (
                await connection.execute(
                    """SELECT ST_Equals(geometry,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) unchanged
                    FROM site_boundary_versions WHERE id=%s""",
                    (json.dumps(aoi.geometry), site["boundary_id"]),
                )
            ).fetchone()
            if unchanged["unchanged"]:
                raise ApiError(
                    409,
                    "boundary_unchanged",
                    "Boundary is unchanged",
                    "The submitted geometry is spatially equal to the current boundary.",
                )
        next_version = int(site["boundary_version"] or 0) + 1
        if site["boundary_id"]:
            await connection.execute(
                "UPDATE site_boundary_versions SET superseded_at=now() WHERE id=%s",
                (site["boundary_id"],),
            )
        try:
            boundary = await (
                await connection.execute(
                    """INSERT INTO site_boundary_versions(
                      organisation_id,site_id,version,geometry,source_authority,
                      source_identifier,source_url,licence,attribution,effective_date,
                      source_crs,validation_result,checksum,created_by,change_reason
                    ) VALUES (
                      %s,%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s,%s,%s,%s,%s,%s,
                      %s,%s,%s,%s,%s
                    ) RETURNING id""",
                    (
                        principal.organisation_id,
                        site_id,
                        next_version,
                        json.dumps(aoi.geometry),
                        payload.source_authority,
                        payload.source_identifier,
                        payload.source_url,
                        payload.licence,
                        payload.attribution,
                        payload.effective_date,
                        payload.source_crs,
                        Jsonb(aoi.validation_result),
                        aoi.checksum,
                        principal.user_id,
                        payload.reason,
                    ),
                )
            ).fetchone()
        except UniqueViolation as error:
            raise ApiError(
                409,
                "boundary_version_conflict",
                "Boundary version conflict",
                "Another boundary version was created concurrently.",
            ) from error
        await connection.execute(
            """UPDATE sites SET current_boundary_version_id=%s,version=version+1,updated_at=now()
            WHERE id=%s""",
            (boundary["id"], site_id),
        )
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="site.boundary_created",
            target_type="site_boundary_version",
            target_id=boundary["id"],
            before={
                "site_id": str(site_id),
                "version": site["boundary_version"],
                "checksum": site["checksum"],
            },
            after={
                "site_id": str(site_id),
                "version": next_version,
                "checksum": aoi.checksum,
                "source_authority": payload.source_authority,
                "source_identifier": payload.source_identifier,
            },
            reason=payload.reason,
            ip_address=_client_ip(request),
        )
        created = await (
            await connection.execute(
                f"""SELECT {_BOUNDARY_COLUMNS}
                FROM site_boundary_versions b JOIN sites s ON s.id=b.site_id
                WHERE b.id=%s""",
                (True, boundary["id"]),
            )
        ).fetchone()
    response.headers["ETag"] = _etag(site_id, expected + 1)
    response.headers["Location"] = f"/api/v1/sites/{site_id}/boundaries"
    return BoundaryResponse(
        data=_boundary_data(created, include_geometry=True), meta=_meta(request)
    )


@router.get("/sites/{site_id}/grids", response_model=GridVersionListResponse)
async def list_site_grids(
    site_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> GridVersionListResponse:
    visibility, visibility_params = _visibility_sql(principal)
    scope = cursor_scope(
        {
            "organisation_id": str(principal.organisation_id),
            "user_id": str(principal.user_id),
            "role": principal.role,
            "site_id": str(site_id),
        }
    )
    position = None
    if cursor:
        try:
            position = decode_cursor(cursor, scope=scope)
        except CursorError as error:
            raise ApiError(
                400,
                "invalid_cursor",
                "Invalid pagination cursor",
                "The cursor is invalid or does not belong to this grid query.",
            ) from error
    cursor_time = position.created_at if position else None
    cursor_id = position.resource_id if position else None
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        rows = await (
            await connection.execute(
                """SELECT g.id,g.version,g.method,g.resolution_metres,g.parameters,
                  g.creation_reason,g.processing_compatibility,g.superseded_at,g.created_at,
                  (g.id=s.current_grid_version_id) is_current,
                  (SELECT count(*) FROM grid_cells c WHERE c.grid_version_id=g.id) cell_count
                FROM grid_versions g JOIN sites s ON s.id=g.site_id
                WHERE g.site_id=%s
                  AND (%s::timestamptz IS NULL OR (g.created_at,g.id)<(%s,%s::uuid))
                ORDER BY g.created_at DESC,g.id DESC LIMIT %s""",
                (site_id, cursor_time, cursor_time, cursor_id, limit + 1),
            )
        ).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = (
        encode_cursor(CursorPosition(page[-1]["created_at"], page[-1]["id"]), scope=scope)
        if has_more
        else None
    )
    return GridVersionListResponse(
        data=[GridVersionData.model_validate(row) for row in page],
        meta=GridVersionListMeta(
            request_id=UUID(request.state.request_id), next_cursor=next_cursor
        ),
    )


@router.post("/sites/{site_id}/grids/generate", response_model=GridVersionResponse, status_code=201)
async def generate_site_grid(
    site_id: UUID,
    payload: GridGenerateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> GridVersionResponse:
    _require_management(principal)
    expected = _expected_version(if_match, site_id)
    visibility, visibility_params = _visibility_sql(principal)
    settings = get_settings()
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params, lock=True)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        if site["version"] != expected:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The site was changed after it was loaded.",
                {"ETag": _etag(site_id, site["version"])},
            )
        if not site["boundary_id"]:
            raise ApiError(
                409,
                "boundary_not_available",
                "Boundary is not available",
                "A site needs a current boundary before a grid can be generated.",
            )
        count = await (
            await connection.execute(
                """WITH boundary AS (
                  SELECT ST_Transform(geometry,6933) geometry FROM site_boundary_versions WHERE id=%s
                ), squares AS (
                  SELECT sq.geom,sq.i,sq.j,b.geometry boundary
                  FROM boundary b CROSS JOIN LATERAL ST_SquareGrid(%s,ST_Envelope(b.geometry)) sq
                  WHERE ST_Intersects(sq.geom,b.geometry)
                ), pieces AS (
                  SELECT i,j,d.geom FROM squares
                  CROSS JOIN LATERAL ST_Dump(CASE WHEN %s THEN ST_CollectionExtract(ST_Intersection(geom,boundary),3) ELSE geom END) d
                ) SELECT count(*) cell_count FROM pieces WHERE ST_Area(geom)>0""",
                (site["boundary_id"], payload.resolution_metres, payload.clip_to_boundary),
            )
        ).fetchone()
        cell_count = int(count["cell_count"])
        if cell_count == 0:
            raise ApiError(
                422,
                "grid_empty",
                "Grid has no cells",
                "The current boundary produced no usable grid cells at this resolution.",
            )
        if cell_count > settings.max_grid_cells:
            raise ApiError(
                422,
                "grid_too_large",
                "Grid exceeds cell limit",
                f"This request would create {cell_count} cells; the configured limit is {settings.max_grid_cells}.",
            )
        next_version = int(
            (
                await (
                    await connection.execute(
                        "SELECT COALESCE(max(version),0)+1 version FROM grid_versions WHERE site_id=%s",
                        (site_id,),
                    )
                ).fetchone()
            )["version"]
        )
        if site["current_grid_version_id"]:
            await connection.execute(
                "UPDATE grid_versions SET superseded_at=now() WHERE id=%s",
                (site["current_grid_version_id"],),
            )
        try:
            grid = await (
                await connection.execute(
                    """INSERT INTO grid_versions(organisation_id,site_id,version,method,resolution_metres,parameters,creation_reason,processing_compatibility)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        principal.organisation_id,
                        site_id,
                        next_version,
                        payload.method,
                        payload.resolution_metres,
                        Jsonb(
                            {
                                "clip_to_boundary": payload.clip_to_boundary,
                                "projection": "EPSG:6933",
                                "cell_key_scheme": "SQ-i-j-part",
                            }
                        ),
                        payload.creation_reason,
                        payload.processing_compatibility,
                    ),
                )
            ).fetchone()
        except UniqueViolation as error:
            raise ApiError(
                409,
                "grid_version_conflict",
                "Grid version conflict",
                "Another grid version was created concurrently.",
            ) from error
        await connection.execute(
            """WITH boundary AS (
              SELECT ST_Transform(geometry,6933) geometry FROM site_boundary_versions WHERE id=%s
            ), squares AS (
              SELECT sq.geom,sq.i,sq.j,b.geometry boundary
              FROM boundary b CROSS JOIN LATERAL ST_SquareGrid(%s,ST_Envelope(b.geometry)) sq
              WHERE ST_Intersects(sq.geom,b.geometry)
            ), pieces AS (
              SELECT i,j,d.path,d.geom FROM squares
              CROSS JOIN LATERAL ST_Dump(CASE WHEN %s THEN ST_CollectionExtract(ST_Intersection(geom,boundary),3) ELSE geom END) d
            ) INSERT INTO grid_cells(organisation_id,grid_version_id,cell_key,display_label,geometry,area_sq_m)
            SELECT %s,%s,format('SQ-%%s-%%s-%%s',i,j,COALESCE(path[1],1)),
              format('Square %%s,%%s',i,j),ST_Transform(geom,4326),ST_Area(geom)
            FROM pieces WHERE ST_Area(geom)>0""",
            (
                site["boundary_id"],
                payload.resolution_metres,
                payload.clip_to_boundary,
                principal.organisation_id,
                grid["id"],
            ),
        )
        await connection.execute(
            "UPDATE sites SET current_grid_version_id=%s,version=version+1,updated_at=now() WHERE id=%s",
            (grid["id"], site_id),
        )
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="site.grid_generated",
            target_type="grid_version",
            target_id=grid["id"],
            after={
                "site_id": str(site_id),
                "version": next_version,
                "method": payload.method,
                "resolution_metres": payload.resolution_metres,
                "cell_count": cell_count,
                "clip_to_boundary": payload.clip_to_boundary,
            },
            reason=payload.creation_reason,
            ip_address=_client_ip(request),
        )
        created = await (
            await connection.execute(
                """SELECT g.id,g.version,g.method,g.resolution_metres,g.parameters,g.creation_reason,g.processing_compatibility,g.superseded_at,g.created_at,TRUE is_current,(SELECT count(*) FROM grid_cells c WHERE c.grid_version_id=g.id) cell_count FROM grid_versions g WHERE g.id=%s""",
                (grid["id"],),
            )
        ).fetchone()
    response.headers["ETag"] = _etag(site_id, expected + 1)
    response.headers["Location"] = f"/api/v1/sites/{site_id}/grids"
    return GridVersionResponse(data=GridVersionData.model_validate(created), meta=_meta(request))


@router.get("/sites/{site_id}/schedule", response_model=ScheduleResponse)
async def get_site_schedule(
    site_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ScheduleResponse:
    visibility, visibility_params = _visibility_sql(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        schedule = await (
            await connection.execute(
                "SELECT id,site_id,cadence,sensor_settings,quality_settings,next_due_at,status,scheduling_version,changed_by,created_at,updated_at FROM monitoring_schedules WHERE site_id=%s AND status<>'archived'",
                (site_id,),
            )
        ).fetchone()
    if not schedule:
        raise ApiError(
            404,
            "schedule_not_found",
            "Schedule not found",
            "This site does not have a monitoring schedule yet.",
        )
    response.headers["ETag"] = _etag(schedule["id"], schedule["scheduling_version"])
    return ScheduleResponse(data=ScheduleData.model_validate(schedule), meta=_meta(request))


@router.put("/sites/{site_id}/schedule", response_model=ScheduleResponse, status_code=200)
async def put_site_schedule(
    site_id: UUID,
    payload: ScheduleUpsertRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> ScheduleResponse:
    _require_management(principal)
    visibility, visibility_params = _visibility_sql(principal)
    now = datetime.now(UTC)
    due_at = _next_due_at(payload.cadence, now)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params, lock=True)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        current = await (
            await connection.execute(
                "SELECT id,cadence,sensor_settings,quality_settings,next_due_at,status,scheduling_version,changed_by FROM monitoring_schedules WHERE site_id=%s AND status<>'archived' FOR UPDATE",
                (site_id,),
            )
        ).fetchone()
        if current and current["status"] == "suspended":
            raise ApiError(
                409,
                "schedule_suspended",
                "Schedule is suspended",
                "Resume the schedule before replacing its active settings.",
            )
        if current:
            expected = _expected_version(if_match, current["id"])
            if current["scheduling_version"] != expected:
                raise ApiError(
                    409,
                    "version_conflict",
                    "Resource version conflict",
                    "The schedule was changed after it was loaded.",
                    {"ETag": _etag(current["id"], current["scheduling_version"])},
                )
            schedule = await (
                await connection.execute(
                    """UPDATE monitoring_schedules SET cadence=%s,sensor_settings=%s,quality_settings=%s,next_due_at=%s,scheduling_version=scheduling_version+1,changed_by=%s,updated_at=now() WHERE id=%s RETURNING id,site_id,cadence,sensor_settings,quality_settings,next_due_at,status,scheduling_version,changed_by,created_at,updated_at""",
                    (
                        payload.cadence,
                        Jsonb(payload.sensor_settings),
                        Jsonb(payload.quality_settings),
                        due_at,
                        principal.user_id,
                        current["id"],
                    ),
                )
            ).fetchone()
            before = {
                "cadence": current["cadence"],
                "sensor_settings": current["sensor_settings"],
                "quality_settings": current["quality_settings"],
                "next_due_at": current["next_due_at"].isoformat(),
            }
            action = "schedule.updated"
        else:
            if if_match is not None:
                raise ApiError(
                    400,
                    "invalid_if_match",
                    "Invalid update precondition",
                    "A schedule does not exist yet, so omit If-Match when creating one.",
                )
            schedule = await (
                await connection.execute(
                    """INSERT INTO monitoring_schedules(organisation_id,site_id,cadence,sensor_settings,quality_settings,next_due_at,changed_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,site_id,cadence,sensor_settings,quality_settings,next_due_at,status,scheduling_version,changed_by,created_at,updated_at""",
                    (
                        principal.organisation_id,
                        site_id,
                        payload.cadence,
                        Jsonb(payload.sensor_settings),
                        Jsonb(payload.quality_settings),
                        due_at,
                        principal.user_id,
                    ),
                )
            ).fetchone()
            before = None
            action = "schedule.created"
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action=action,
            target_type="monitoring_schedule",
            target_id=schedule["id"],
            before=before,
            after={
                "cadence": schedule["cadence"],
                "sensor_settings": schedule["sensor_settings"],
                "quality_settings": schedule["quality_settings"],
                "next_due_at": schedule["next_due_at"].isoformat(),
                "status": schedule["status"],
                "scheduling_version": schedule["scheduling_version"],
            },
            ip_address=_client_ip(request),
        )
    response.headers["ETag"] = _etag(schedule["id"], schedule["scheduling_version"])
    return ScheduleResponse(data=ScheduleData.model_validate(schedule), meta=_meta(request))


async def _change_schedule_state(
    *,
    site_id: UUID,
    request: Request,
    principal: Principal,
    if_match: str | None,
    target_status: str,
    suspension_reason: str | None = None,
) -> ScheduleData:
    _require_management(principal)
    visibility, visibility_params = _visibility_sql(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params, lock=True)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        current = await (
            await connection.execute(
                "SELECT id,site_id,cadence,sensor_settings,quality_settings,next_due_at,status,scheduling_version,changed_by,created_at,updated_at FROM monitoring_schedules WHERE site_id=%s AND status<>'archived' FOR UPDATE",
                (site_id,),
            )
        ).fetchone()
        if not current:
            raise ApiError(
                404,
                "schedule_not_found",
                "Schedule not found",
                "This site does not have a monitoring schedule yet.",
            )
        expected = _expected_version(if_match, current["id"])
        if current["scheduling_version"] != expected:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The schedule was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["scheduling_version"])},
            )
        if current["status"] == target_status:
            raise ApiError(
                409,
                "schedule_state_conflict",
                "Schedule state conflict",
                f"The schedule is already {target_status}.",
            )
        if target_status == "suspended" and current["status"] != "active":
            raise ApiError(
                409,
                "schedule_state_conflict",
                "Schedule state conflict",
                "Only an active schedule can be suspended.",
            )
        if target_status == "active" and current["status"] != "suspended":
            raise ApiError(
                409,
                "schedule_state_conflict",
                "Schedule state conflict",
                "Only a suspended schedule can be resumed.",
            )
        next_due_at = (
            _next_due_at(current["cadence"], datetime.now(UTC))
            if target_status == "active"
            else current["next_due_at"]
        )
        updated = await (
            await connection.execute(
                """UPDATE monitoring_schedules SET status=%s,suspension_reason=%s,next_due_at=%s,scheduling_version=scheduling_version+1,changed_by=%s,updated_at=now() WHERE id=%s RETURNING id,site_id,cadence,sensor_settings,quality_settings,next_due_at,status,scheduling_version,changed_by,created_at,updated_at""",
                (target_status, suspension_reason, next_due_at, principal.user_id, current["id"]),
            )
        ).fetchone()
        action = "schedule.suspended" if target_status == "suspended" else "schedule.resumed"
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action=action,
            target_type="monitoring_schedule",
            target_id=current["id"],
            before={"status": current["status"], "next_due_at": current["next_due_at"].isoformat()},
            after={
                "status": updated["status"],
                "next_due_at": updated["next_due_at"].isoformat(),
                "scheduling_version": updated["scheduling_version"],
            },
            reason=suspension_reason,
            ip_address=_client_ip(request),
        )
    return ScheduleData.model_validate(updated)


@router.post("/sites/{site_id}/schedule/suspend", response_model=ScheduleResponse)
async def suspend_site_schedule(
    site_id: UUID,
    payload: ScheduleSuspendRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> ScheduleResponse:
    schedule = await _change_schedule_state(
        site_id=site_id,
        request=request,
        principal=principal,
        if_match=if_match,
        target_status="suspended",
        suspension_reason=payload.reason,
    )
    response.headers["ETag"] = _etag(schedule.id, schedule.scheduling_version)
    return ScheduleResponse(data=schedule, meta=_meta(request))


@router.post("/sites/{site_id}/schedule/resume", response_model=ScheduleResponse)
async def resume_site_schedule(
    site_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> ScheduleResponse:
    schedule = await _change_schedule_state(
        site_id=site_id,
        request=request,
        principal=principal,
        if_match=if_match,
        target_status="active",
    )
    response.headers["ETag"] = _etag(schedule.id, schedule.scheduling_version)
    return ScheduleResponse(data=schedule, meta=_meta(request))


@router.delete("/sites/{site_id}/schedule", status_code=204)
async def archive_site_schedule(
    site_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> Response:
    _require_management(principal)
    visibility, visibility_params = _visibility_sql(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params, lock=True)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        current = await (
            await connection.execute(
                "SELECT id,status,scheduling_version FROM monitoring_schedules WHERE site_id=%s AND status<>'archived' FOR UPDATE",
                (site_id,),
            )
        ).fetchone()
        if not current:
            raise ApiError(
                404,
                "schedule_not_found",
                "Schedule not found",
                "This site does not have a monitoring schedule yet.",
            )
        expected = _expected_version(if_match, current["id"])
        if current["scheduling_version"] != expected:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The schedule was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["scheduling_version"])},
            )
        await connection.execute(
            "UPDATE monitoring_schedules SET status='archived',suspension_reason=NULL,scheduling_version=scheduling_version+1,changed_by=%s,updated_at=now() WHERE id=%s",
            (principal.user_id, current["id"]),
        )
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="schedule.archived",
            target_type="monitoring_schedule",
            target_id=current["id"],
            before={
                "status": current["status"],
                "scheduling_version": current["scheduling_version"],
            },
            after={"status": "archived", "scheduling_version": current["scheduling_version"] + 1},
            ip_address=_client_ip(request),
        )
    return Response(status_code=204)


@router.get("/sites/{site_id}/grid-cells", response_model=GridCellListResponse)
async def list_grid_cells(
    site_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    grid_version_id: Annotated[UUID | None, Query()] = None,
    bbox: Annotated[str | None, Query()] = None,
    cell_key: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> GridCellListResponse:
    bounds = _bbox(bbox)
    normalised_key = cell_key.strip() if cell_key else None
    if bounds is None and not normalised_key:
        raise ApiError(
            422,
            "grid_query_filter_required",
            "Grid-cell filter required",
            "Provide bbox for map browsing or cell_key for an exact grid-cell lookup.",
        )
    visibility, visibility_params = _visibility_sql(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, visibility_params)
        if not site:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        resolved_grid_id = grid_version_id or site["current_grid_version_id"]
        if resolved_grid_id is None:
            raise ApiError(
                409,
                "grid_not_available",
                "Grid is not available",
                "This site has no current grid version yet.",
            )
        grid = await (
            await connection.execute(
                "SELECT id FROM grid_versions WHERE id=%s AND site_id=%s",
                (resolved_grid_id, site_id),
            )
        ).fetchone()
        if not grid:
            raise ApiError(
                404,
                "grid_version_not_found",
                "Grid version not found",
                "The grid version does not belong to this site or is unavailable.",
            )
        scope = cursor_scope(
            {
                "organisation_id": str(principal.organisation_id),
                "user_id": str(principal.user_id),
                "role": principal.role,
                "site_id": str(site_id),
                "grid_version_id": str(resolved_grid_id),
                "bbox": bounds,
                "cell_key": normalised_key,
            }
        )
        position = None
        if cursor:
            try:
                position = decode_cursor(cursor, scope=scope)
            except CursorError as error:
                raise ApiError(
                    400,
                    "invalid_cursor",
                    "Invalid pagination cursor",
                    "The cursor is invalid or does not belong to this grid-cell query.",
                ) from error
        cursor_time = position.created_at if position else None
        cursor_id = position.resource_id if position else None
        bbox_values = bounds or (None, None, None, None)
        rows = await (
            await connection.execute(
                """SELECT c.id,c.grid_version_id,c.cell_key,c.display_label,
                  ST_AsGeoJSON(c.geometry,9,0)::jsonb geometry,c.area_sq_m,c.created_at
                FROM grid_cells c
                WHERE c.grid_version_id=%s
                  AND (%s::text IS NULL OR c.cell_key=%s)
                  AND (%s::double precision IS NULL OR (
                    c.geometry && ST_MakeEnvelope(%s,%s,%s,%s,4326)
                    AND ST_Intersects(c.geometry,ST_MakeEnvelope(%s,%s,%s,%s,4326))
                  ))
                  AND (%s::timestamptz IS NULL OR (c.created_at,c.id)<(%s,%s::uuid))
                ORDER BY c.created_at DESC,c.id DESC LIMIT %s""",
                (
                    resolved_grid_id,
                    normalised_key,
                    normalised_key,
                    bbox_values[0],
                    *bbox_values,
                    *bbox_values,
                    cursor_time,
                    cursor_time,
                    cursor_id,
                    limit + 1,
                ),
            )
        ).fetchall()
    page, has_more = rows[:limit], len(rows) > limit
    next_cursor = (
        encode_cursor(CursorPosition(page[-1]["created_at"], page[-1]["id"]), scope=scope)
        if has_more
        else None
    )
    return GridCellListResponse(
        data=[GridCellData.model_validate(row) for row in page],
        meta=GridCellListMeta(
            request_id=UUID(request.state.request_id),
            next_cursor=next_cursor,
            grid_version_id=resolved_grid_id,
        ),
    )


@router.get("/sites/{site_id}", response_model=SiteResponse)
async def get_site(
    site_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> SiteResponse:
    visibility, params = _visibility_sql(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        site = await _load_site(connection, site_id, visibility, params)
    if not site:
        raise ApiError(
            404,
            "site_not_found",
            "Site not found",
            "The site does not exist or is not available to you.",
        )
    response.headers["ETag"] = _etag(site["id"], site["version"])
    return SiteResponse(data=_site_data(site, include_geometry=True), meta=_meta(request))


@router.patch("/sites/{site_id}", response_model=SiteResponse)
async def update_site(
    site_id: UUID,
    payload: SiteUpdateRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
    if_match: Annotated[str | None, Header()] = None,
) -> SiteResponse:
    _require_management(principal)
    expected = _expected_version(if_match, site_id)
    visibility, params = _visibility_sql(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        current = await _load_site(connection, site_id, visibility, params, lock=True)
        if not current:
            raise ApiError(
                404,
                "site_not_found",
                "Site not found",
                "The site does not exist or is not available to you.",
            )
        if current["version"] != expected:
            raise ApiError(
                409,
                "version_conflict",
                "Resource version conflict",
                "The site was changed after it was loaded.",
                {"ETag": _etag(current["id"], current["version"])},
            )
        values = payload.model_dump(exclude_unset=True, exclude={"reason"})
        department_id = values.get("managing_department_id", current["managing_department_id"])
        department = await (
            await connection.execute("SELECT status FROM departments WHERE id=%s", (department_id,))
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
                "A site must have an active managing department.",
            )
        after = {
            field: values.get(field, current[field])
            for field in ("name", "slug", "description", "sensitivity", "managing_department_id")
        }
        try:
            await connection.execute(
                """UPDATE sites SET name=%s,slug=%s,description=%s,sensitivity=%s,managing_department_id=%s,version=version+1,updated_at=now() WHERE id=%s""",
                (
                    after["name"],
                    after["slug"],
                    after["description"],
                    after["sensitivity"],
                    after["managing_department_id"],
                    site_id,
                ),
            )
        except UniqueViolation as error:
            raise ApiError(
                409,
                "site_slug_conflict",
                "Site slug already exists",
                "Another site already uses this slug.",
            ) from error
        await record_audit(
            connection,
            organisation_id=principal.organisation_id,
            actor_id=principal.user_id,
            action="site.updated",
            target_type="site",
            target_id=site_id,
            before={
                field: str(current[field]) if isinstance(current[field], UUID) else current[field]
                for field in after
            },
            after={
                field: str(value) if isinstance(value, UUID) else value
                for field, value in after.items()
            },
            reason=payload.reason,
            ip_address=_client_ip(request),
        )
        updated = await _load_site(connection, site_id, visibility, params)
    response.headers["ETag"] = _etag(updated["id"], updated["version"])
    return SiteResponse(data=_site_data(updated, include_geometry=True), meta=_meta(request))
