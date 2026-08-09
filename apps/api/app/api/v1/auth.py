import hmac
import ipaddress
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from ...schemas.auth import (
    AccessTokenData,
    AccessTokenResponse,
    EmptyData,
    EmptyResponse,
    InvitationAcceptedData,
    InvitationAcceptedResponse,
    InvitationAcceptRequest,
    InvitationSummaryData,
    InvitationSummaryResponse,
    LoginRequest,
    PasswordResetCompleteRequest,
    PasswordResetRequest,
    PasswordResetRequestData,
    PasswordResetRequestResponse,
    ProfileData,
    ProfileResponse,
    RefreshRequest,
    ResponseMeta,
    TeamSummary,
)
from ...security.auth import AuthError, AuthService, TokenPair
from ...security.tokens import new_opaque_token
from ...settings import get_settings
from ..dependencies import (
    Principal,
    current_principal,
    find_organisation_id,
    resolve_organisation_id,
)
from ..errors import ApiError

router = APIRouter(tags=["authentication"])
auth_service = AuthService()


def _client_ip(request: Request) -> str | None:
    if not request.client:
        return None
    try:
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=UUID(request.state.request_id))


def _set_session_cookies(response: Response, pair: TokenPair) -> None:
    settings = get_settings()
    max_age = max(0, int((pair.refresh_expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=pair.refresh_token,
        max_age=max_age,
        expires=pair.refresh_expires_at,
        path="/api/v1/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=new_opaque_token(),
        max_age=max_age,
        expires=pair.refresh_expires_at,
        path="/api/v1/auth",
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
    )


def _clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    for name in (settings.refresh_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            key=name,
            path="/api/v1/auth",
            secure=settings.cookie_secure,
            httponly=name == settings.refresh_cookie_name,
            samesite=settings.cookie_samesite,
        )


def _token_response(request: Request, pair: TokenPair) -> AccessTokenResponse:
    return AccessTokenResponse(
        data=AccessTokenData(
            access_token=pair.access_token,
            expires_at=pair.access_expires_at,
            session_id=pair.session_id,
        ),
        meta=_meta(request),
    )


@router.post(
    "/auth/password-resets",
    response_model=PasswordResetRequestResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
) -> PasswordResetRequestResponse:
    organisation_id = await find_organisation_id(payload.organisation_slug)
    raw_token = None
    if organisation_id is not None:
        raw_token = await auth_service.request_password_reset(
            organisation_id=organisation_id,
            email=payload.email,
            ip_address=_client_ip(request),
        )
    development_token = raw_token if get_settings().environment == "local" else None
    return PasswordResetRequestResponse(
        data=PasswordResetRequestData(development_token=development_token),
        meta=_meta(request),
    )


@router.post("/auth/password-resets/complete", response_model=EmptyResponse)
async def complete_password_reset(
    payload: PasswordResetCompleteRequest,
    request: Request,
) -> EmptyResponse:
    organisation_id = await find_organisation_id(payload.organisation_slug)
    if organisation_id is None:
        raise ApiError(
            400,
            "invalid_reset_token",
            "Password reset failed",
            "The reset token is invalid or expired.",
        )
    try:
        await auth_service.reset_password(
            organisation_id=organisation_id,
            token=payload.token,
            new_password=payload.new_password,
        )
    except AuthError as error:
        raise ApiError(
            400,
            "invalid_reset_token",
            "Password reset failed",
            "The reset token is invalid or expired.",
        ) from error
    return EmptyResponse(data=EmptyData(), meta=_meta(request))


@router.get(
    "/invitations/{token}/summary",
    response_model=InvitationSummaryResponse,
)
async def invitation_summary(
    request: Request,
    token: Annotated[str, Path(min_length=32, max_length=512)],
    organisation_slug: Annotated[str, Query(min_length=1, max_length=120)],
) -> InvitationSummaryResponse:
    organisation_id = await find_organisation_id(organisation_slug)
    if organisation_id is None:
        raise ApiError(
            404,
            "invalid_invitation",
            "Invitation unavailable",
            "The invitation is invalid or expired.",
        )
    try:
        summary = await auth_service.invitation_summary(
            organisation_id=organisation_id,
            token=token,
        )
    except AuthError as error:
        raise ApiError(
            404,
            "invalid_invitation",
            "Invitation unavailable",
            "The invitation is invalid or expired.",
        ) from error
    return InvitationSummaryResponse(
        data=InvitationSummaryData(
            masked_email=summary.masked_email,
            role=summary.role,
            organisation_name=summary.organisation_name,
            department_name=summary.department_name,
            expires_at=summary.expires_at,
        ),
        meta=_meta(request),
    )


@router.post(
    "/invitations/{token}/accept",
    response_model=InvitationAcceptedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def accept_invitation(
    payload: InvitationAcceptRequest,
    request: Request,
    token: Annotated[str, Path(min_length=32, max_length=512)],
) -> InvitationAcceptedResponse:
    organisation_id = await find_organisation_id(payload.organisation_slug)
    if organisation_id is None:
        raise ApiError(
            400,
            "invalid_invitation",
            "Invitation acceptance failed",
            "The invitation is invalid or expired.",
        )
    try:
        user_id = await auth_service.accept_invitation(
            organisation_id=organisation_id,
            token=token,
            display_name=payload.display_name,
            password=payload.password,
        )
    except AuthError as error:
        raise ApiError(
            400,
            "invalid_invitation",
            "Invitation acceptance failed",
            "The invitation is invalid or expired.",
        ) from error
    return InvitationAcceptedResponse(
        data=InvitationAcceptedData(user_id=user_id),
        meta=_meta(request),
    )


@router.post("/auth/login", response_model=AccessTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
) -> AccessTokenResponse:
    organisation_id = await resolve_organisation_id(payload.organisation_slug)
    try:
        pair = await auth_service.login(
            organisation_id=organisation_id,
            email=payload.email,
            password=payload.password,
            user_agent=request.headers.get("User-Agent"),
            ip_address=_client_ip(request),
        )
    except AuthError as error:
        raise ApiError(
            401,
            "invalid_credentials",
            "Authentication failed",
            "The supplied credentials are invalid.",
        ) from error
    _set_session_cookies(response, pair)
    return _token_response(request, pair)


@router.post("/auth/refresh", response_model=AccessTokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
) -> AccessTokenResponse:
    settings = get_settings()
    body_token = payload.refresh_token if settings.environment == "local" else None
    cookie_token = request.cookies.get(settings.refresh_cookie_name)
    refresh_token = body_token or cookie_token
    if not refresh_token:
        raise ApiError(
            401,
            "invalid_refresh_token",
            "Authentication failed",
            "A valid refresh session is required.",
        )
    if body_token is None:
        csrf_cookie = request.cookies.get(settings.csrf_cookie_name, "")
        csrf_header = request.headers.get("X-CSRF-Token", "")
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            raise ApiError(
                403,
                "csrf_validation_failed",
                "Request verification failed",
                "The CSRF token is missing or invalid.",
            )
    organisation_id = await resolve_organisation_id(payload.organisation_slug)
    try:
        pair = await auth_service.refresh(
            organisation_id=organisation_id,
            refresh_token=refresh_token,
            user_agent=request.headers.get("User-Agent"),
            ip_address=_client_ip(request),
        )
    except AuthError as error:
        _clear_session_cookies(response)
        raise ApiError(
            401,
            "invalid_refresh_token",
            "Authentication failed",
            "The refresh session is invalid or expired.",
        ) from error
    _set_session_cookies(response, pair)
    return _token_response(request, pair)


@router.post("/auth/logout", response_model=EmptyResponse)
async def logout(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(current_principal)],
) -> EmptyResponse:
    await auth_service.revoke_session(
        organisation_id=principal.organisation_id,
        user_id=principal.user_id,
        session_id=principal.session_id,
    )
    _clear_session_cookies(response)
    return EmptyResponse(data=EmptyData(), meta=_meta(request))


@router.get("/me", response_model=ProfileResponse, tags=["profile"])
async def me(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
) -> ProfileResponse:
    return ProfileResponse(
        data=ProfileData(
            id=principal.user_id,
            organisation_id=principal.organisation_id,
            email=principal.email,
            display_name=principal.display_name,
            role=principal.role,
            status=principal.status,
            timezone=principal.timezone,
            department_id=principal.department_id,
            department_name=principal.department_name,
            teams=[TeamSummary.model_validate(team) for team in principal.teams],
        ),
        meta=_meta(request),
    )
