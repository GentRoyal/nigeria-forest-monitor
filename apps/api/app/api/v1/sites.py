import ipaddress
import json
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ...db import tenant_connection
from ...schemas.auth import ResponseMeta
from ...schemas.sites import (
    BoundaryData,
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
  s.version,s.created_at,s.updated_at,
  COALESCE((SELECT jsonb_agg(jsonb_build_object('id',t.id,'name',t.name) ORDER BY t.name)
    FROM site_tags st JOIN tags t ON t.id=st.tag_id WHERE st.site_id=s.id),'[]'::jsonb) tags,
  b.id boundary_id,b.version boundary_version,
  CASE WHEN b.id IS NULL THEN NULL ELSE ST_AsGeoJSON(b.geometry,9,0)::jsonb END boundary_geometry,
  b.source_authority,b.source_identifier,b.source_url,b.licence,b.attribution,
  b.effective_date,b.source_crs,b.checksum,b.validation_result,b.created_at boundary_created_at,
  CASE WHEN b.id IS NULL THEN NULL ELSE ST_Area(b.geometry::geography)/1000000.0 END area_sq_km,
  CASE WHEN b.id IS NULL THEN NULL ELSE jsonb_build_object(
    'west',ST_XMin(Box2D(b.geometry)),'south',ST_YMin(Box2D(b.geometry)),
    'east',ST_XMax(Box2D(b.geometry)),'north',ST_YMax(Box2D(b.geometry))) END bounds
"""


def _site_data(row: dict[str, Any], *, include_geometry: bool) -> SiteData:
    boundary = None
    if row["boundary_id"]:
        boundary = BoundaryData(
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
            created_at=row["boundary_created_at"],
        )
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
                    """INSERT INTO site_boundary_versions(organisation_id,site_id,version,geometry,source_authority,source_identifier,source_url,licence,attribution,effective_date,source_crs,validation_result,checksum)
                VALUES (%s,%s,1,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
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
