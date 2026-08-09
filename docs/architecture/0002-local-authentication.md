# ADR 0002: Local PostgreSQL-backed authentication

- Status: Accepted
- Scope: Local MVP authentication and authorization boundary

## Context

The first deployment serves one private government/entity workspace and runs
locally. Every user belongs to exactly one organisation. Hosted identity
providers, social login, and government SSO are not required for the local MVP,
but authentication must be real rather than simulated with development users.

## Decision

FastAPI owns an invitation-only authentication service backed by the product
PostgreSQL database.

- Passwords are hashed with Argon2id using versioned parameters.
- Access tokens are short-lived and contain only stable identity/session claims.
- Refresh tokens rotate on use and are stored only as hashes.
- Reuse of an invalidated refresh token revokes its token family.
- Invitations and password-reset tokens are single-use, expiring, and stored as
  hashes.
- Login, logout, invitation, password reset, role change, suspension, failed
  authentication, and session revocation are audited.
- Roles and resource access are resolved from current database state; a stale
  token cannot grant permissions removed from the database.
- API keys and Airflow service identities are distinct from interactive user
  sessions.
- Authentication responses and logs never expose whether an uninvited email
  address exists beyond the minimum required invitation workflow.

## Account lifecycle

```text
invited -> active -> suspended -> active
                    \-> disabled
invited -> expired
```

- Only administrators invite, suspend, reactivate, or disable organisation
  users, subject to preventing removal of the last active administrator.
- Suspension revokes active sessions immediately.
- Disabled accounts cannot be reactivated without an explicit audited
  administrative action.
- Accepting an invitation binds the account permanently to that invitation's
  organisation and department.

## Session lifecycle

```text
active -> refreshed -> active
active -> logged_out
active -> revoked
active -> expired
```

Password reset, account suspension, detected refresh-token reuse, or an
administrator's revoke-all action invalidates applicable active sessions.

## Security controls required before private beta

- Rate limits and progressive delay for authentication endpoints
- Secure, HTTP-only, same-site cookies where browser token handling requires them
- CSRF protection for cookie-authenticated state changes
- Password-policy and breached-password checks
- Secret rotation and environment-specific signing keys
- Generic authentication errors and safe structured audit logs
- Tests for invitation replay, reset replay, refresh reuse, role changes,
  suspension, cross-organisation denial, and sensitive-site denial

## Migration seam

Authentication is exposed behind an internal identity-provider interface. Domain
records reference the application's stable user ID rather than provider-specific
identifiers. A later government OIDC/SAML or managed identity integration must
map into the same user profile, organisation, department, team, role, and audit
model.

## Deferred decisions

- Multi-factor authentication
- Government OIDC or SAML integration
- Managed authentication provider
- Account recovery requiring administrator approval

## Consequences

The local system has no hosted identity dependency and all persistence remains
in PostgreSQL. In return, the application assumes responsibility for credential,
token, session, rate-limit, recovery, and audit security until authentication is
migrated to an approved identity provider.
