from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from ...db import tenant_connection
from ...schemas.auth import EmptyData, EmptyResponse, ResponseMeta
from ...schemas.invitations import (
    InvitationCreatedData,
    InvitationCreatedResponse,
    InvitationCreateRequest,
    InvitationData,
    InvitationListData,
    InvitationListResponse,
)
from ...security.audit import record_audit
from ...security.auth import AuthError, AuthService
from ...security.permissions import Action, Role, is_allowed
from ...security.tokens import hash_opaque_token
from ...settings import get_settings
from ..dependencies import Principal, current_principal
from ..errors import ApiError

router = APIRouter(tags=["invitations"])
auth_service = AuthService()


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=UUID(request.state.request_id))


def _require_member_management(principal: Principal) -> None:
    if not is_allowed(principal.role, Action.MANAGE_MEMBERS):
        raise ApiError(
            403,
            "permission_denied",
            "Permission denied",
            "Your role cannot manage invitations.",
        )


def _invitation_data(row: dict) -> InvitationData:
    return InvitationData.model_validate(row)


@router.get("/invitations", response_model=InvitationListResponse)
async def list_invitations(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    invitation_status: Annotated[
        Literal["pending", "accepted", "revoked", "expired"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvitationListResponse:
    _require_member_management(principal)
    status_expression = """
        CASE
          WHEN i.accepted_at IS NOT NULL THEN 'accepted'
          WHEN i.revoked_at IS NOT NULL THEN 'revoked'
          WHEN i.expires_at<=now() THEN 'expired'
          ELSE 'pending'
        END
    """
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        total = await (
            await connection.execute(
                f"""
                SELECT count(*) count FROM invitations i
                WHERE (%s::text IS NULL OR ({status_expression})=%s)
                """,
                (invitation_status, invitation_status),
            )
        ).fetchone()
        invitations = await (
            await connection.execute(
                f"""
                SELECT i.id,i.organisation_id,i.department_id,d.name department_name,
                  i.email::text email,i.role,({status_expression}) status,
                  i.invited_by,u.display_name invited_by_name,i.expires_at,
                  i.accepted_at,i.revoked_at,i.created_at
                FROM invitations i
                JOIN departments d ON d.id=i.department_id
                JOIN user_profiles u ON u.id=i.invited_by
                WHERE (%s::text IS NULL OR ({status_expression})=%s)
                ORDER BY i.created_at DESC,i.id LIMIT %s OFFSET %s
                """,
                (invitation_status, invitation_status, limit, offset),
            )
        ).fetchall()
    return InvitationListResponse(
        data=InvitationListData(
            items=[_invitation_data(invitation) for invitation in invitations],
            total=total["count"],
            limit=limit,
            offset=offset,
        ),
        meta=_meta(request),
    )


@router.post(
    "/invitations",
    response_model=InvitationCreatedResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    payload: InvitationCreateRequest,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> InvitationCreatedResponse:
    _require_member_management(principal)
    try:
        raw_token = await auth_service.create_invitation(
            organisation_id=principal.organisation_id,
            department_id=payload.department_id,
            email=payload.email,
            role=Role(payload.role),
            invited_by=principal.user_id,
        )
    except AuthError as error:
        errors = {
            "department_not_found": (
                404,
                "department_not_found",
                "Department not found",
                "The requested department does not exist.",
            ),
            "department_archived": (
                409,
                "department_archived",
                "Department is archived",
                "Invitations require an active department.",
            ),
            "member_exists": (
                409,
                "member_exists",
                "Member already exists",
                "A member with this email already exists.",
            ),
            "invitation_exists": (
                409,
                "invitation_exists",
                "Invitation already exists",
                "A pending invitation already exists for this email.",
            ),
        }
        mapped = errors.get(error.code)
        if not mapped:
            raise ApiError(403, "permission_denied", "Permission denied", "Access denied.") from error
        raise ApiError(*mapped) from error
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        invitation = await (
            await connection.execute(
                """
                SELECT i.id,i.organisation_id,i.department_id,d.name department_name,
                  i.email::text email,i.role,'pending' status,i.invited_by,
                  u.display_name invited_by_name,i.expires_at,i.accepted_at,
                  i.revoked_at,i.created_at
                FROM invitations i
                JOIN departments d ON d.id=i.department_id
                JOIN user_profiles u ON u.id=i.invited_by
                WHERE i.token_hash=%s
                """,
                (hash_opaque_token(raw_token),),
            )
        ).fetchone()
    development_token = raw_token if get_settings().environment == "local" else None
    return InvitationCreatedResponse(
        data=InvitationCreatedData(
            **invitation,
            development_token=development_token,
        ),
        meta=_meta(request),
    )


@router.delete("/invitations/{invitation_id}", response_model=EmptyResponse)
async def revoke_invitation(
    invitation_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> EmptyResponse:
    _require_member_management(principal)
    async with tenant_connection(principal.organisation_id, principal.user_id) as connection:
        invitation = await (
            await connection.execute(
                """
                SELECT id,email::text email,accepted_at,revoked_at
                FROM invitations WHERE id=%s FOR UPDATE
                """,
                (invitation_id,),
            )
        ).fetchone()
        if not invitation:
            raise ApiError(
                404,
                "invitation_not_found",
                "Invitation not found",
                "The invitation does not exist.",
            )
        if invitation["accepted_at"] is not None:
            raise ApiError(
                409,
                "invitation_already_accepted",
                "Invitation already accepted",
                "An accepted invitation cannot be revoked.",
            )
        if invitation["revoked_at"] is None:
            await connection.execute(
                "UPDATE invitations SET revoked_at=now() WHERE id=%s",
                (invitation_id,),
            )
            await record_audit(
                connection,
                organisation_id=principal.organisation_id,
                actor_id=principal.user_id,
                action="invitation.revoked",
                target_type="invitation",
                target_id=invitation_id,
                before={"email": invitation["email"], "status": "pending"},
                after={"email": invitation["email"], "status": "revoked"},
            )
    return EmptyResponse(data=EmptyData(), meta=_meta(request))
