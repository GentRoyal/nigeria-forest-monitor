from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from psycopg.errors import UniqueViolation

from ..db import tenant_connection
from ..settings import get_settings
from .audit import record_audit
from .passwords import hash_password, password_needs_rehash, verify_password
from .permissions import Action, Role, is_allowed
from .tokens import hash_opaque_token, issue_access_token, new_opaque_token


class AuthError(Exception):
    """A safe authentication failure whose message may be returned to a client."""

    def __init__(self, code: str, message: str = "authentication failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    session_id: UUID


@dataclass(frozen=True)
class InvitationSummary:
    invitation_id: UUID
    masked_email: str
    role: str
    organisation_name: str
    department_name: str
    expires_at: datetime


class AuthService:
    async def invitation_summary(
        self,
        *,
        organisation_id: UUID,
        token: str,
    ) -> InvitationSummary:
        async with tenant_connection(organisation_id) as connection:
            invitation = await (
                await connection.execute(
                    """
                    SELECT
                      i.id,i.email::text email,i.role,i.expires_at,
                      o.name organisation_name,d.name department_name
                    FROM invitations i
                    JOIN organisations o ON o.id=i.organisation_id
                    JOIN departments d
                      ON d.organisation_id=i.organisation_id
                     AND d.id=i.department_id
                    WHERE i.token_hash=%s
                      AND i.accepted_at IS NULL
                      AND i.revoked_at IS NULL
                      AND i.expires_at>now()
                    """,
                    (hash_opaque_token(token),),
                )
            ).fetchone()
        if not invitation:
            raise AuthError("invalid_invitation", "invitation is invalid or expired")
        local, separator, domain = invitation["email"].partition("@")
        masked_local = f"{local[:1]}***" if local else "***"
        masked_email = f"{masked_local}{separator}{domain}" if separator else masked_local
        return InvitationSummary(
            invitation_id=invitation["id"],
            masked_email=masked_email,
            role=invitation["role"],
            organisation_name=invitation["organisation_name"],
            department_name=invitation["department_name"],
            expires_at=invitation["expires_at"],
        )

    async def _active_actor(self, connection, organisation_id: UUID, actor_id: UUID) -> dict:
        row = await (
            await connection.execute(
                """
                SELECT id, role, status FROM user_profiles
                WHERE organisation_id=%s AND id=%s
                """,
                (organisation_id, actor_id),
            )
        ).fetchone()
        if not row or row["status"] != "active":
            raise AuthError("forbidden", "permission denied")
        return row

    async def _require(
        self,
        connection,
        organisation_id: UUID,
        actor_id: UUID,
        action: Action,
    ) -> dict:
        actor = await self._active_actor(connection, organisation_id, actor_id)
        if not is_allowed(actor["role"], action):
            raise AuthError("forbidden", "permission denied")
        return actor

    async def create_invitation(
        self,
        *,
        organisation_id: UUID,
        department_id: UUID,
        email: str,
        role: Role,
        invited_by: UUID,
    ) -> str:
        if role == Role.OWNER:
            raise AuthError("forbidden", "owner invitations are not supported")
        raw_token = new_opaque_token()
        expires_at = datetime.now(UTC) + timedelta(hours=get_settings().invitation_hours)
        async with tenant_connection(organisation_id, invited_by) as connection:
            await self._require(connection, organisation_id, invited_by, Action.MANAGE_MEMBERS)
            department = await (
                await connection.execute(
                    "SELECT status FROM departments WHERE id=%s",
                    (department_id,),
                )
            ).fetchone()
            if not department:
                raise AuthError("department_not_found", "department not found")
            if department["status"] != "active":
                raise AuthError("department_archived", "department is archived")
            existing_member = await (
                await connection.execute(
                    "SELECT id FROM user_profiles WHERE email=%s",
                    (email.strip().lower(),),
                )
            ).fetchone()
            if existing_member:
                raise AuthError("member_exists", "a member with this email already exists")
            await connection.execute(
                """
                UPDATE invitations SET revoked_at=now()
                WHERE email=%s AND accepted_at IS NULL AND revoked_at IS NULL
                  AND expires_at<=now()
                """,
                (email.strip().lower(),),
            )
            try:
                row = await (
                    await connection.execute(
                        """
                        INSERT INTO invitations (
                          organisation_id,department_id,email,role,token_hash,
                          invited_by,expires_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        RETURNING id
                        """,
                        (
                            organisation_id,
                            department_id,
                            email.strip().lower(),
                            role.value,
                            hash_opaque_token(raw_token),
                            invited_by,
                            expires_at,
                        ),
                    )
                ).fetchone()
            except UniqueViolation as error:
                raise AuthError(
                    "invitation_exists", "an active invitation already exists"
                ) from error
            await record_audit(
                connection,
                organisation_id=organisation_id,
                actor_id=invited_by,
                action="invitation.created",
                target_type="invitation",
                target_id=row["id"],
                after={"email": email.strip().lower(), "role": role.value},
            )
        return raw_token

    async def accept_invitation(
        self,
        *,
        organisation_id: UUID,
        token: str,
        display_name: str,
        password: str,
    ) -> UUID:
        token_hash = hash_opaque_token(token)
        failure: AuthError | None = None
        user_id: UUID | None = None
        async with tenant_connection(organisation_id) as connection:
            invitation = await (
                await connection.execute(
                    """
                    SELECT * FROM invitations
                    WHERE organisation_id=%s AND token_hash=%s
                    FOR UPDATE
                    """,
                    (organisation_id, token_hash),
                )
            ).fetchone()
            now = datetime.now(UTC)
            if (
                not invitation
                or invitation["accepted_at"] is not None
                or invitation["revoked_at"] is not None
                or invitation["expires_at"] <= now
            ):
                failure = AuthError("invalid_invitation", "invitation is invalid or expired")
            else:
                user_id = uuid4()
                try:
                    await connection.execute(
                        """
                        INSERT INTO user_profiles (
                          id,organisation_id,primary_department_id,email,display_name,
                          role,status,invited_at,activated_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s)
                        """,
                        (
                            user_id,
                            organisation_id,
                            invitation["department_id"],
                            invitation["email"],
                            display_name.strip(),
                            invitation["role"],
                            invitation["created_at"],
                            now,
                        ),
                    )
                    await connection.execute(
                        """
                        INSERT INTO auth_credentials (
                          user_id,organisation_id,password_hash
                        ) VALUES (%s,%s,%s)
                        """,
                        (user_id, organisation_id, hash_password(password)),
                    )
                except UniqueViolation:
                    failure = AuthError("invalid_invitation", "invitation is invalid or expired")
                if failure is None:
                    await connection.execute(
                        "UPDATE invitations SET accepted_at=%s WHERE id=%s",
                        (now, invitation["id"]),
                    )
                    await record_audit(
                        connection,
                        organisation_id=organisation_id,
                        actor_id=user_id,
                        action="invitation.accepted",
                        target_type="user_profile",
                        target_id=user_id,
                    )
        if failure:
            raise failure
        assert user_id is not None
        return user_id

    async def login(
        self,
        *,
        organisation_id: UUID,
        email: str,
        password: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        pair: TokenPair | None = None
        failure = False
        async with tenant_connection(organisation_id) as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT u.id,u.status,c.password_hash,c.failed_attempts,c.locked_until,c.status credential_status
                    FROM user_profiles u JOIN auth_credentials c ON c.user_id=u.id
                    WHERE u.organisation_id=%s AND u.email=%s
                    FOR UPDATE OF c
                    """,
                    (organisation_id, email.strip().lower()),
                )
            ).fetchone()
            now = datetime.now(UTC)
            valid = bool(
                row
                and row["status"] == "active"
                and row["credential_status"] == "active"
                and (row["locked_until"] is None or row["locked_until"] <= now)
                and verify_password(row["password_hash"], password)
            )
            if not valid:
                failure = True
                if row:
                    attempts = row["failed_attempts"] + 1
                    locked_until = now + timedelta(minutes=15) if attempts >= 5 else None
                    await connection.execute(
                        """
                        UPDATE auth_credentials
                        SET failed_attempts=%s,locked_until=%s,
                            status=CASE WHEN %s THEN 'locked' ELSE status END
                        WHERE user_id=%s
                        """,
                        (attempts, locked_until, locked_until is not None, row["id"]),
                    )
                    await record_audit(
                        connection,
                        organisation_id=organisation_id,
                        actor_id=row["id"],
                        action="authentication.failed",
                        target_type="user_profile",
                        target_id=row["id"],
                        ip_address=ip_address,
                    )
            else:
                if password_needs_rehash(row["password_hash"]):
                    await connection.execute(
                        "UPDATE auth_credentials SET password_hash=%s WHERE user_id=%s",
                        (hash_password(password), row["id"]),
                    )
                await connection.execute(
                    """
                    UPDATE auth_credentials SET failed_attempts=0,locked_until=NULL,status='active'
                    WHERE user_id=%s
                    """,
                    (row["id"],),
                )
                pair = await self._create_session(
                    connection,
                    organisation_id=organisation_id,
                    user_id=row["id"],
                    family_id=uuid4(),
                    user_agent=user_agent,
                    ip_address=ip_address,
                )
                await record_audit(
                    connection,
                    organisation_id=organisation_id,
                    actor_id=row["id"],
                    action="authentication.succeeded",
                    target_type="auth_session",
                    target_id=pair.session_id,
                    ip_address=ip_address,
                )
        if failure or pair is None:
            raise AuthError("invalid_credentials")
        return pair

    async def _create_session(
        self,
        connection,
        *,
        organisation_id: UUID,
        user_id: UUID,
        family_id: UUID,
        user_agent: str | None,
        ip_address: str | None,
    ) -> TokenPair:
        raw_refresh = new_opaque_token()
        refresh_expires = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
        row = await (
            await connection.execute(
                """
                INSERT INTO auth_sessions (
                  organisation_id,user_id,token_family_id,refresh_token_hash,
                  user_agent,ip_address,expires_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    organisation_id,
                    user_id,
                    family_id,
                    hash_opaque_token(raw_refresh),
                    user_agent,
                    ip_address,
                    refresh_expires,
                ),
            )
        ).fetchone()
        access, access_expires = issue_access_token(
            user_id=user_id,
            organisation_id=organisation_id,
            session_id=row["id"],
        )
        return TokenPair(access, access_expires, raw_refresh, refresh_expires, row["id"])

    async def refresh(
        self,
        *,
        organisation_id: UUID,
        refresh_token: str,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> TokenPair:
        pair: TokenPair | None = None
        failure: AuthError | None = None
        async with tenant_connection(organisation_id) as connection:
            session = await (
                await connection.execute(
                    """
                    SELECT s.*,u.status user_status
                    FROM auth_sessions s JOIN user_profiles u ON u.id=s.user_id
                    WHERE s.organisation_id=%s AND s.refresh_token_hash=%s
                    FOR UPDATE OF s
                    """,
                    (organisation_id, hash_opaque_token(refresh_token)),
                )
            ).fetchone()
            now = datetime.now(UTC)
            if not session:
                failure = AuthError("invalid_refresh_token")
            elif session["revoked_at"] is not None or session["replaced_by_session_id"] is not None:
                await connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at=COALESCE(revoked_at,%s),
                      revoke_reason='refresh token reuse'
                    WHERE token_family_id=%s AND revoked_at IS NULL
                    """,
                    (now, session["token_family_id"]),
                )
                await record_audit(
                    connection,
                    organisation_id=organisation_id,
                    actor_id=session["user_id"],
                    action="authentication.refresh_reuse",
                    target_type="auth_session",
                    target_id=session["id"],
                    ip_address=ip_address,
                )
                failure = AuthError("invalid_refresh_token")
            elif session["expires_at"] <= now or session["user_status"] != "active":
                await connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at=%s,revoke_reason='expired or inactive account'
                    WHERE id=%s
                    """,
                    (now, session["id"]),
                )
                failure = AuthError("invalid_refresh_token")
            else:
                pair = await self._create_session(
                    connection,
                    organisation_id=organisation_id,
                    user_id=session["user_id"],
                    family_id=session["token_family_id"],
                    user_agent=user_agent,
                    ip_address=ip_address,
                )
                await connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at=%s,revoke_reason='rotated',
                      replaced_by_session_id=%s,last_activity_at=%s
                    WHERE id=%s
                    """,
                    (now, pair.session_id, now, session["id"]),
                )
                await record_audit(
                    connection,
                    organisation_id=organisation_id,
                    actor_id=session["user_id"],
                    action="authentication.refreshed",
                    target_type="auth_session",
                    target_id=pair.session_id,
                )
        if failure or pair is None:
            raise failure or AuthError("invalid_refresh_token")
        return pair

    async def revoke_all_sessions(
        self,
        *,
        organisation_id: UUID,
        user_id: UUID,
        actor_id: UUID,
        reason: str,
    ) -> None:
        async with tenant_connection(organisation_id, actor_id) as connection:
            actor = await self._active_actor(connection, organisation_id, actor_id)
            if actor_id != user_id and not is_allowed(actor["role"], Action.MANAGE_MEMBERS):
                raise AuthError("forbidden", "permission denied")
            await connection.execute(
                """
                UPDATE auth_sessions SET revoked_at=now(),revoke_reason=%s
                WHERE organisation_id=%s AND user_id=%s AND revoked_at IS NULL
                """,
                (reason, organisation_id, user_id),
            )
            await record_audit(
                connection,
                organisation_id=organisation_id,
                actor_id=actor_id,
                action="authentication.sessions_revoked",
                target_type="user_profile",
                target_id=user_id,
                reason=reason,
            )

    async def revoke_session(
        self,
        *,
        organisation_id: UUID,
        user_id: UUID,
        session_id: UUID,
        reason: str = "logged out",
    ) -> None:
        async with tenant_connection(organisation_id, user_id) as connection:
            session = await (
                await connection.execute(
                    """
                    SELECT id,revoked_at FROM auth_sessions
                    WHERE id=%s AND user_id=%s
                    FOR UPDATE
                    """,
                    (session_id, user_id),
                )
            ).fetchone()
            if not session:
                return
            if session["revoked_at"] is None:
                await connection.execute(
                    """
                    UPDATE auth_sessions
                    SET revoked_at=now(),revoke_reason=%s
                    WHERE id=%s
                    """,
                    (reason, session_id),
                )
                await record_audit(
                    connection,
                    organisation_id=organisation_id,
                    actor_id=user_id,
                    action="authentication.logged_out",
                    target_type="auth_session",
                    target_id=session_id,
                    reason=reason,
                )

    async def change_member_status(
        self,
        *,
        organisation_id: UUID,
        user_id: UUID,
        status: str,
        actor_id: UUID,
        reason: str,
    ) -> None:
        if status not in {"active", "suspended", "disabled"}:
            raise ValueError("unsupported member status")
        async with tenant_connection(organisation_id, actor_id) as connection:
            await self._require(connection, organisation_id, actor_id, Action.MANAGE_MEMBERS)
            target = await (
                await connection.execute(
                    "SELECT role,status FROM user_profiles WHERE id=%s FOR UPDATE",
                    (user_id,),
                )
            ).fetchone()
            if not target:
                raise AuthError("not_found", "member not found")
            if target["role"] == Role.OWNER and status != "active":
                active_owners = await (
                    await connection.execute(
                        """
                        SELECT count(*) count FROM user_profiles
                        WHERE role='owner' AND status='active'
                        """
                    )
                ).fetchone()
                if active_owners["count"] <= 1:
                    raise AuthError("last_owner", "the last active owner cannot be disabled")
            await connection.execute(
                "UPDATE user_profiles SET status=%s,updated_at=now() WHERE id=%s",
                (status, user_id),
            )
            if status != "active":
                await connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at=now(),revoke_reason=%s
                    WHERE user_id=%s AND revoked_at IS NULL
                    """,
                    (f"account {status}", user_id),
                )
            await record_audit(
                connection,
                organisation_id=organisation_id,
                actor_id=actor_id,
                action=f"membership.{status}",
                target_type="user_profile",
                target_id=user_id,
                before={"status": target["status"]},
                after={"status": status},
                reason=reason,
            )

    async def request_password_reset(
        self,
        *,
        organisation_id: UUID,
        email: str,
        ip_address: str | None = None,
    ) -> str | None:
        """Create reset material for the delivery layer; callers must respond generically."""
        raw_token = new_opaque_token()
        async with tenant_connection(organisation_id) as connection:
            user = await (
                await connection.execute(
                    """
                    SELECT id FROM user_profiles
                    WHERE organisation_id=%s AND email=%s AND status='active'
                    """,
                    (organisation_id, email.strip().lower()),
                )
            ).fetchone()
            if not user:
                return None
            await connection.execute(
                """
                UPDATE password_reset_tokens SET invalidated_at=now()
                WHERE user_id=%s AND consumed_at IS NULL AND invalidated_at IS NULL
                """,
                (user["id"],),
            )
            row = await (
                await connection.execute(
                    """
                    INSERT INTO password_reset_tokens(
                      organisation_id,user_id,token_hash,expires_at,request_ip
                    ) VALUES (%s,%s,%s,%s,%s) RETURNING id
                    """,
                    (
                        organisation_id,
                        user["id"],
                        hash_opaque_token(raw_token),
                        datetime.now(UTC)
                        + timedelta(minutes=get_settings().password_reset_minutes),
                        ip_address,
                    ),
                )
            ).fetchone()
            await record_audit(
                connection,
                organisation_id=organisation_id,
                actor_id=user["id"],
                action="authentication.password_reset_requested",
                target_type="password_reset_token",
                target_id=row["id"],
                ip_address=ip_address,
            )
        return raw_token

    async def change_member_role(
        self,
        *,
        organisation_id: UUID,
        user_id: UUID,
        role: Role,
        actor_id: UUID,
        reason: str,
    ) -> None:
        async with tenant_connection(organisation_id, actor_id) as connection:
            await self._require(connection, organisation_id, actor_id, Action.MANAGE_MEMBERS)
            target = await (
                await connection.execute(
                    "SELECT role,status FROM user_profiles WHERE id=%s FOR UPDATE",
                    (user_id,),
                )
            ).fetchone()
            if not target:
                raise AuthError("not_found", "member not found")
            if target["role"] == Role.OWNER and role != Role.OWNER:
                active_owners = await (
                    await connection.execute(
                        "SELECT count(*) count FROM user_profiles WHERE role='owner' AND status='active'"
                    )
                ).fetchone()
                if active_owners["count"] <= 1:
                    raise AuthError("last_owner", "the last active owner cannot be demoted")
            await connection.execute(
                "UPDATE user_profiles SET role=%s,updated_at=now() WHERE id=%s",
                (role.value, user_id),
            )
            await connection.execute(
                """
                UPDATE auth_sessions SET revoked_at=now(),revoke_reason='role changed'
                WHERE user_id=%s AND revoked_at IS NULL
                """,
                (user_id,),
            )
            await record_audit(
                connection,
                organisation_id=organisation_id,
                actor_id=actor_id,
                action="membership.role_changed",
                target_type="user_profile",
                target_id=user_id,
                before={"role": target["role"]},
                after={"role": role.value},
                reason=reason,
            )

    async def reset_password(
        self,
        *,
        organisation_id: UUID,
        token: str,
        new_password: str,
    ) -> None:
        new_hash = hash_password(new_password)
        failure = False
        async with tenant_connection(organisation_id) as connection:
            reset = await (
                await connection.execute(
                    """
                    SELECT * FROM password_reset_tokens
                    WHERE organisation_id=%s AND token_hash=%s
                    FOR UPDATE
                    """,
                    (organisation_id, hash_opaque_token(token)),
                )
            ).fetchone()
            now = datetime.now(UTC)
            if (
                not reset
                or reset["consumed_at"] is not None
                or reset["invalidated_at"] is not None
                or reset["expires_at"] <= now
            ):
                failure = True
            else:
                await connection.execute(
                    """
                    UPDATE auth_credentials SET password_hash=%s,hash_version=hash_version+1,
                      password_changed_at=%s,failed_attempts=0,locked_until=NULL,status='active'
                    WHERE user_id=%s
                    """,
                    (new_hash, now, reset["user_id"]),
                )
                await connection.execute(
                    """
                    UPDATE password_reset_tokens
                    SET consumed_at=CASE WHEN id=%s THEN %s ELSE consumed_at END,
                        invalidated_at=CASE WHEN id<>%s AND consumed_at IS NULL THEN %s ELSE invalidated_at END
                    WHERE user_id=%s
                    """,
                    (reset["id"], now, reset["id"], now, reset["user_id"]),
                )
                await connection.execute(
                    """
                    UPDATE auth_sessions SET revoked_at=%s,revoke_reason='password reset'
                    WHERE user_id=%s AND revoked_at IS NULL
                    """,
                    (now, reset["user_id"]),
                )
                await record_audit(
                    connection,
                    organisation_id=organisation_id,
                    actor_id=reset["user_id"],
                    action="authentication.password_reset_completed",
                    target_type="user_profile",
                    target_id=reset["user_id"],
                )
        if failure:
            raise AuthError("invalid_reset_token", "reset token is invalid or expired")
