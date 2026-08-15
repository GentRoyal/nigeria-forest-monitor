# Phase 4 API Contract Design

Status: proposed contract for approval before endpoint implementation.

This document defines the backend HTTP contract for the private Nigeria Forest
Monitor MVP. PostgreSQL/PostGIS remains authoritative, FastAPI is the only
product API boundary, and Airflow remains an internal orchestrator. This
document does not define client-side implementation.

## 1. Design goals

- Make the complete approved monitoring workflow operable through OpenAPI.
- Enforce organisation isolation in both PostgreSQL RLS and application logic.
- Resolve roles and resource access from current database state.
- Make mutations idempotent, auditable, and safe to retry.
- Preserve immutable spatial, analytical, and review history.
- Keep automated signals distinct from human corroboration and institutional
  verification.
- Keep local filesystem storage replaceable by hosted object storage.
- Keep Airflow implementation details outside the public product contract.

## 2. API boundary

Public product routes use:

~~~text
/api/v1
~~~

Health routes remain unversioned:

~~~text
GET /health/live
GET /health/ready
~~~

Internal worker routes use a separate namespace and authentication mechanism:

~~~text
/internal/v1
~~~

Airflow never authenticates as an interactive user. Product users never call
Airflow directly.

## 3. Resource model

Primary resources:

- authentication sessions
- password resets
- invitations
- current user profile
- organisation
- departments
- teams and memberships
- sites
- site boundary versions
- grid versions and cells
- monitoring schedules
- catalogue items and observations
- processing jobs and runs
- change events
- assignments
- reviews
- comments and evidence
- subscriptions and notification preferences
- notifications
- exports
- API keys
- audit events

Resource identifiers are UUIDs. Human-readable slugs are secondary identifiers
and never replace UUIDs in foreign keys.

## 4. Authentication contract

### Proposed browser session model

- Access token: short-lived JWT sent as Authorization: Bearer.
- Refresh token: rotating opaque token stored in a Secure, HttpOnly, SameSite
  cookie when the API is used by a browser.
- OpenAPI/manual clients may send the refresh token in an explicit request body
  only in local development.
- State-changing cookie-authenticated operations require CSRF protection.
- Access-token claims contain user ID, organisation ID, and session ID only.
- Every authenticated request reloads session, membership, account status, and
  role from PostgreSQL.

This retains good OpenAPI usability while preventing browser JavaScript from
reading the long-lived refresh token.

### Authentication endpoints

| Method | Path | Purpose | Access |
|---|---|---|---|
| POST | /api/v1/auth/login | Start a local session | Public, rate limited |
| POST | /api/v1/auth/refresh | Rotate refresh token | Active refresh session |
| POST | /api/v1/auth/logout | Revoke current session | Authenticated |
| POST | /api/v1/auth/logout-all | Revoke all user sessions | Authenticated |
| POST | /api/v1/auth/password-resets | Request reset delivery | Public, generic response |
| POST | /api/v1/auth/password-resets/complete | Consume reset token | Public, rate limited |
| GET | /api/v1/invitations/{token}/summary | Safe invitation summary | Valid invitation |
| POST | /api/v1/invitations/{token}/accept | Create invited account | Valid invitation |

The eight Slice 4.1 authentication/profile endpoints are implemented. The
self-service `PATCH /me`, session listing, and single-session revocation
endpoints from Slice 4.2 are also implemented, along with organisation
read/update, department administration, and team administration. Logout-all,
member directory/updates, and team membership assignment are also implemented.
Logout-all and invitation list/create/revoke are also implemented. Until email
delivery is connected, only the local environment returns development reset or
invitation tokens; non-local responses never expose raw tokens.

The privileged, organisation-scoped audit query is implemented at
`GET /api/v1/admin/audit-events` with signed cursor pagination, safe filters,
summary redaction, and owner/administrator access. This completes Slice 4.2.

Login request:

~~~json
{
  "organisation_slug": "nfm-local-pilot",
  "email": "analyst@example.gov.ng",
  "password": "user supplied password"
}
~~~

Successful login response:

~~~json
{
  "data": {
    "access_token": "JWT",
    "token_type": "Bearer",
    "expires_at": "2026-08-09T15:30:00Z",
    "session_id": "uuid"
  }
}
~~~

Authentication failures return one generic public message. Logs and audit
events retain safe diagnostic categories without recording passwords or raw
tokens.

## 5. Request context and authorisation

For each authenticated request:

1. Verify JWT signature, issuer, audience, and expiry.
2. Load the referenced session under the claimed organisation.
3. Reject expired or revoked sessions.
4. Load the current user profile and membership.
5. Reject suspended, disabled, or expired users.
6. Set transaction-local organisation and user settings for RLS.
7. Resolve the requested capability from current role/resource state.
8. Execute the domain operation and audit it in the same transaction.

The API does not trust a role claim from a token.

### Roles

| Capability | Owner | Administrator | Analyst | Verification officer | Viewer |
|---|---:|---:|---:|---:|---:|
| Manage organisation | Yes | Yes | No | No | No |
| Manage members/teams | Yes | Yes | No | No | No |
| Manage sites/schedules | Yes | Yes | No | No | No |
| View normal sites | Yes | Yes | Yes | Assigned referral only | Approved summaries |
| View sensitive sites | Yes | Yes | Granted team only | Assigned referral only | Approved summaries |
| Submit remote review | No | No | Yes | No | No |
| Submit institutional verification | No | No | No | Assigned referral only | No |
| View approved summaries | Yes | Yes | Yes | Assigned referral | Yes |
| Cancel monitoring work | Yes | Yes | No | No | No |

Owner is the protected organisation root role. The last active owner cannot be
disabled or demoted.

## 6. Response envelope

Single resource:

~~~json
{
  "data": {
    "id": "uuid",
    "type": "site",
    "name": "Old Oyo National Park"
  },
  "meta": {
    "request_id": "uuid"
  }
}
~~~

Collection:

~~~json
{
  "data": [],
  "meta": {
    "request_id": "uuid",
    "next_cursor": "opaque-or-null",
    "has_more": false
  }
}
~~~

The API never returns database row objects directly. Response models explicitly
select public fields so new database columns cannot leak automatically.

## 7. Error contract

Errors use application/problem+json:

~~~json
{
  "type": "https://nigeria-forest-monitor.local/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/sites",
  "code": "validation_error",
  "request_id": "uuid",
  "errors": [
    {
      "field": "boundary.geometry",
      "code": "invalid_geometry",
      "message": "Geometry must be a valid Polygon or MultiPolygon."
    }
  ]
}
~~~

Stable application codes include:

- invalid_credentials
- session_expired
- permission_denied
- resource_not_found
- validation_error
- invalid_state_transition
- idempotency_conflict
- version_conflict
- duplicate_resource
- rate_limited
- upstream_unavailable
- job_lease_conflict

Cross-tenant and unauthorised sensitive-resource lookups normally return 404 so
the API does not reveal that the resource exists.

## 8. Request tracing and logging

Every request receives a UUID request_id. An accepted X-Request-ID must be a
valid bounded identifier; otherwise the API generates one.

Structured log fields:

- timestamp
- level
- service
- environment
- request_id
- correlation_id
- route template
- method
- status
- duration_ms
- organisation_id when authenticated
- actor_id when authenticated
- safe error code

Never log:

- passwords
- raw access/refresh/reset/invitation tokens
- full sensitive geometries
- email notification contents containing location details

## 9. Pagination, filtering, and sorting

Collections use opaque cursor pagination rather than page numbers.

Common query parameters:

~~~text
limit=50
cursor=opaque
sort=-created_at
created_after=ISO-8601
created_before=ISO-8601
status=value
q=search text
~~~

Rules:

- Default limit: 50.
- Maximum limit: 200.
- Cursor encodes the stable sort value and UUID tiebreaker.
- Cursor is signed so clients cannot alter internal pagination state.
- Sort fields are allow-listed per endpoint.
- Filters are applied before the cursor predicate.

Spatial collection filters:

~~~text
bbox=min_lon,min_lat,max_lon,max_lat
intersects=<GeoJSON geometry>
~~~

The API rejects invalid latitude/longitude ranges, self-intersecting geometry,
empty geometry, excessive vertex counts, and geometries larger than the
configured request limit.

## 10. Idempotency

Mutation endpoints that may be retried accept:

~~~text
Idempotency-Key: caller-generated-key
~~~

Required for:

- manual job creation
- export requests
- invitation creation
- asset-upload initiation
- worker completion callbacks
- event state transitions

The server stores:

- organisation
- actor
- route/operation
- idempotency key
- canonical request hash
- response status and body reference
- expiry

Same key and same request returns the original result. Same key with a different
request returns 409 idempotency_conflict.

Database domain uniqueness remains authoritative. Idempotency records improve
HTTP retry behaviour but do not replace unique constraints.

## 11. Optimistic concurrency

Mutable resources expose an integer version and ETag:

~~~text
ETag: "site-uuid:7"
~~~

Updates require:

~~~text
If-Match: "site-uuid:7"
~~~

The update includes:

~~~sql
WHERE id = $1 AND version = 7
~~~

and increments version atomically. A stale writer receives 409 version_conflict
with the current ETag.

Immutable resources such as submitted reviews, boundary versions, grid
versions, and audit events are never updated in place.

## 12. Organisation and identity endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/me | Current profile, role, department, teams |
| PATCH | /api/v1/me | Update display name/timezone |
| GET | /api/v1/me/sessions | List safe session/device metadata |
| DELETE | /api/v1/me/sessions/{session_id} | Revoke one session |
| GET | /api/v1/organisation | Organisation profile |
| PATCH | /api/v1/organisation | Update allowed settings |
| GET | /api/v1/departments | List departments |
| POST | /api/v1/departments | Create department |
| PATCH | /api/v1/departments/{id} | Rename/archive department |
| GET | /api/v1/teams | List teams |
| POST | /api/v1/teams | Create team |
| PATCH | /api/v1/teams/{id} | Rename/archive team |
| GET | /api/v1/members | Search/list members |
| GET | /api/v1/members/{id} | Member detail |
| PATCH | /api/v1/members/{id} | Change role/status/department |
| PUT | /api/v1/teams/{id}/members/{user_id} | Add team membership |
| DELETE | /api/v1/teams/{id}/members/{user_id} | Remove membership |
| GET | /api/v1/invitations | List invitations |
| POST | /api/v1/invitations | Create invitation |
| DELETE | /api/v1/invitations/{id} | Revoke invitation |

Department changes must remove or replace team memberships that would violate
the same-department rule in one transaction.

## 13. Site endpoints

Implementation status: list, create, detail, versioned metadata update,
boundary history, and validated immutable boundary replacement are implemented.
Lifecycle, grids, team grants, and timeline remain later batches.

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/sites | Search/filter visible sites |
| POST | /api/v1/sites | Create custom or predefined site record |
| GET | /api/v1/sites/{id} | Site detail and monitoring health |
| PATCH | /api/v1/sites/{id} | Update mutable site metadata |
| DELETE | /api/v1/sites/{id} | Begin audited 30-day soft deletion |
| POST | /api/v1/sites/{id}/restore | Restore during recovery window |
| GET | /api/v1/sites/{id}/timeline | Unified site history |
| GET | /api/v1/sites/{id}/boundaries | Boundary-version history |
| POST | /api/v1/sites/{id}/boundaries | Add validated immutable boundary |
| GET | /api/v1/sites/{id}/grids | Grid-version history |
| POST | /api/v1/sites/{id}/grids/generate | Generate grid from current boundary |
| POST | /api/v1/sites/{id}/grids/import | Import validated cells |
| GET | /api/v1/sites/{id}/grid-cells | Query cells by viewport/key |
| PUT | /api/v1/sites/{id}/team-grants/{team_id} | Grant sensitive-site access |
| DELETE | /api/v1/sites/{id}/team-grants/{team_id} | Remove grant |

### Site creation

~~~json
{
  "name": "Authorised Community Forest",
  "slug": "authorised-community-forest",
  "description": "Customer supplied authorised monitoring area",
  "origin": "custom",
  "sensitivity": "sensitive",
  "managing_department_id": "uuid",
  "tags": ["pilot", "priority"],
  "boundary": {
    "geometry": {
      "type": "MultiPolygon",
      "coordinates": []
    },
    "source_authority": "Submitting institution",
    "source_identifier": "reference",
    "licence": "authorised internal use",
    "attribution": "Submitting institution",
    "source_crs": "EPSG:4326",
    "effective_date": "2026-08-09"
  }
}
~~~

Site and first boundary are created atomically. A custom site requires source,
authority, licence, attribution, CRS, checksum, and validation metadata.

Boundary history omits geometry by default; clients explicitly request it with
`include_geometry=true`. Adding a replacement requires the current site ETag
in `If-Match` and a human-readable `reason`. The previous version is retained
and only its `superseded_at` lifecycle marker may change.

### AOI validation

Validation includes:

- GeoJSON Polygon or MultiPolygon only.
- Input CRS declared and transformed to EPSG:4326.
- Valid, non-empty geometry.
- Coordinates within longitude/latitude bounds after transformation.
- Configured maximum area and vertex count.
- Duplicate/consecutive-point cleanup where safe.
- Ring orientation normalisation.
- Checksum over canonical geometry plus source metadata.
- Explicit rejection rather than silently repairing material topology errors.

The API never accepts a boundary scraped approximately from a webpage.

### Grid generation

Grid generation request:

~~~json
{
  "method": "square",
  "resolution_metres": 1000,
  "clip_to_boundary": true,
  "creation_reason": "initial monitoring grid",
  "processing_compatibility": "v1"
}
~~~

Grid generation creates an immutable grid version and stable cell keys. Changing
the grid later creates another version; historical observations continue to
reference the prior version.

Grid version history and map cell retrieval are implemented. Map requests must
supply either a viewport `bbox` or an exact `cell_key`; responses are keyset
paginated and include GeoJSON cell geometry. Grid generation/import remain
separate write endpoints.

## 14. Tags, search, and saved filters

Proposed endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/tags | List organisation tags |
| POST | /api/v1/tags | Create tag |
| PUT | /api/v1/sites/{id}/tags/{tag_id} | Attach tag |
| DELETE | /api/v1/sites/{id}/tags/{tag_id} | Detach tag |
| GET | /api/v1/saved-filters | List personal filters |
| POST | /api/v1/saved-filters | Save a validated filter definition |
| PATCH | /api/v1/saved-filters/{id} | Rename/update filter |
| DELETE | /api/v1/saved-filters/{id} | Delete filter |

Search uses PostgreSQL text search/trigram support for names, slugs, and
authorised metadata. Sensitive coordinates and evidence bodies are not placed
in broad search indexes.

## 15. Monitoring schedule endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/sites/{id}/schedule | Current schedule |
| PUT | /api/v1/sites/{id}/schedule | Create/replace active settings |
| POST | /api/v1/sites/{id}/schedule/suspend | Suspend with reason |
| POST | /api/v1/sites/{id}/schedule/resume | Resume from current time |
| DELETE | /api/v1/sites/{id}/schedule | Archive schedule |
| POST | /api/v1/sites/{id}/jobs | Manually request processing |

Schedule request:

~~~json
{
  "cadence": "weekly",
  "sensor_settings": {
    "preferred_sensors": ["sentinel-1", "sentinel-2"]
  },
  "quality_settings": {
    "minimum_coverage": 0.9,
    "maximum_cloud_cover": 20
  }
}
~~~

Rules:

- Cadence is weekly, fortnightly, or monthly.
- Administrator/owner changes are audited.
- Schedule change recalculates next_due_at from change time.
- Running jobs are not changed or cancelled.
- Suspension requires a reason.
- Resume does not backfill missed observations.
- Manual processing of a suspended site requires administrator override plus an
  explicit warning acknowledgement.

## 16. Observation and raster catalogue endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/sites/{id}/observations | Visible observation history |
| GET | /api/v1/observations/{id} | Quality, eligibility, lineage |
| GET | /api/v1/observations/{id}/grid-cells | Per-cell measurements |
| GET | /api/v1/observations/{id}/assets | Authorised asset metadata |
| POST | /api/v1/observations/{id}/retry | Explicit retry after failure |
| GET | /api/v1/catalogue-items/{id} | Source catalogue provenance |

The normal product API does not create source catalogue records manually.
Discovery workers create or deduplicate them through authenticated internal
operations.

Observation filters:

- acquisition time
- sensor/provider
- eligibility
- status
- coverage threshold
- quality flags
- grid version

Source hrefs are returned only when the source licence and access classification
allow them.

## 17. Change-event endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/events | Role-filtered review queue |
| GET | /api/v1/events/{id} | Event detail, evidence, lineage |
| POST | /api/v1/events/{id}/transitions | Perform explicit state transition |
| GET | /api/v1/events/{id}/assignments | Assignment history |
| POST | /api/v1/events/{id}/assignments | Assign analyst/officer |
| POST | /api/v1/events/{id}/assignments/{assignment_id}/accept | Accept |
| POST | /api/v1/events/{id}/assignments/{assignment_id}/decline | Decline |
| POST | /api/v1/events/{id}/assignments/{assignment_id}/cancel | Cancel |
| GET | /api/v1/events/{id}/reviews | Immutable submitted reviews |
| POST | /api/v1/events/{id}/reviews | Submit review |
| POST | /api/v1/events/{id}/reviews/{review_id}/supersede | Correct review |
| GET | /api/v1/events/{id}/comments | Authorised discussion |
| POST | /api/v1/events/{id}/comments | Add auditable comment |
| GET | /api/v1/events/{id}/evidence | Evidence metadata |
| POST | /api/v1/events/{id}/evidence | Register authorised evidence |

### Event transition request

~~~json
{
  "to_status": "remotely_corroborated",
  "reason": "Persistent change appears in two observations and optical imagery.",
  "review_id": "uuid"
}
~~~

Rules:

- Every transition requires an actor, reason, timestamp, and audit event.
- Automated actors may create new events but cannot corroborate, dismiss,
  institutionally verify, or resolve them.
- Analysts may perform only remote-analysis decisions.
- Institutional verification requires an accepted assignment to an authorised
  verification officer.
- The platform does not generate field instructions or patrol routes.
- Event categories describe observable signals, not intent or illegality.

### Review request

~~~json
{
  "review_type": "remote_analysis",
  "decision": "awaiting_more_observations",
  "rationale": "The signal overlaps seasonal flooding.",
  "confidence_statement": "Insufficient evidence for remote corroboration.",
  "supporting_evidence_ids": ["uuid"]
}
~~~

Submitted reviews are immutable. Corrections create a new review that references
the superseded review.

## 18. Processing-job public endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/jobs | List role-visible product jobs |
| POST | /api/v1/sites/{id}/jobs | Create idempotent manual job |
| GET | /api/v1/jobs/{id} | Product status and safe progress |
| POST | /api/v1/jobs/{id}/cancel | Explicit audited cancellation |
| POST | /api/v1/jobs/{id}/retry | Create retry attempt |
| GET | /api/v1/jobs/{id}/runs | Orchestration/processing lineage |

Manual job request:

~~~json
{
  "job_type": "processing",
  "observation_id": "uuid",
  "processing_version": "v1",
  "priority": 5,
  "suspended_site_override": false,
  "override_warning_acknowledged": false
}
~~~

The server derives the domain idempotency key from organisation, site,
observation, job type, grid version, and processing version. The HTTP
Idempotency-Key protects network retries around that operation.

Retry creates another orchestration attempt without overwriting prior run
history.

## 19. Internal worker API

Internal routes require scoped service credentials, network restriction, and
request correlation. They do not accept interactive JWTs.

| Method | Path | Purpose |
|---|---|---|
| POST | /internal/v1/jobs/{id}/claim | Obtain lease for queued work |
| POST | /internal/v1/jobs/{id}/heartbeat | Extend active lease/progress |
| POST | /internal/v1/jobs/{id}/stages | Record idempotent stage callback |
| POST | /internal/v1/jobs/{id}/complete | Atomically publish successful result |
| POST | /internal/v1/jobs/{id}/fail | Record safe failure/retry decision |
| POST | /internal/v1/catalogue-items/upsert | Deduplicate discovered source item |
| POST | /internal/v1/observations/upsert | Publish suitability assessment |
| POST | /internal/v1/events | Publish possible-change signal |
| POST | /internal/v1/assets | Register source/derived asset metadata |

### Lease model

A claim records:

- worker identity
- opaque lease token hash
- claimed_at
- lease_expires_at
- heartbeat_at
- attempt number

Heartbeat requires the current lease token. Completion and failure are rejected
for expired/replaced leases unless an administrator has explicitly reconciled
the job.

### Completion rules

Job completion occurs in one database transaction:

1. Verify service identity and job lease.
2. Verify job is in an allowed non-terminal state.
3. Verify callback idempotency key/request hash.
4. Register processing run, outputs, metrics, warnings, and checksums.
5. Register derived raster metadata.
6. Register grid observations/events as applicable.
7. Set job to completed only after metadata commits.
8. Write audit/operational event.

An Airflow task reporting success is not sufficient by itself.

## 20. Asset-provider interface

The API depends on an AssetStore interface rather than local filesystem calls in
route handlers.

~~~python
class AssetStore(Protocol):
    async def create_upload_reference(
        self,
        *,
        organisation_id: UUID,
        object_key: str,
        content_type: str,
        size_limit: int,
        expires_in: timedelta,
    ) -> UploadReference: ...

    async def create_download_reference(
        self,
        *,
        organisation_id: UUID,
        object_key: str,
        expires_in: timedelta,
    ) -> DownloadReference: ...
~~~

Local implementation:

- Creates short-lived opaque upload/download grants.
- Maps only to files under an organisation-scoped storage root.
- Rejects traversal and symlink escape.
- Verifies content type, declared size, actual size, and checksum.

Future hosted implementation:

- Returns signed object-storage URLs.
- Uses the same API response shape.
- Keeps raw bucket credentials away from clients.

Proposed endpoints:

| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/assets/uploads | Create controlled upload grant |
| POST | /api/v1/assets/uploads/{id}/complete | Verify and register upload |
| POST | /api/v1/assets/{id}/download | Create authorised download grant |

Downloads always re-evaluate the requester's current access. An old exported or
notification URL cannot widen access.

## 21. Notifications and subscriptions

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/me/notification-preferences | Current defaults |
| PUT | /api/v1/me/notification-preferences | Update allowed channels/digests |
| GET | /api/v1/subscriptions | List subscriptions |
| POST | /api/v1/subscriptions | Subscribe to site/event |
| DELETE | /api/v1/subscriptions/{id} | Unsubscribe |
| GET | /api/v1/notifications | List in-app notifications |
| POST | /api/v1/notifications/{id}/read | Mark read |
| POST | /api/v1/notifications/read-all | Mark visible set read |

Email content contains a safe summary and authenticated application link, never
sensitive coordinates.

## 22. Exports

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/exports | List requester's exports |
| POST | /api/v1/exports | Create audited asynchronous export |
| GET | /api/v1/exports/{id} | Status and expiry |
| POST | /api/v1/exports/{id}/download | Authorised short-lived download |
| DELETE | /api/v1/exports/{id} | Expire/delete permitted output |

An export snapshots the requester's authorised scope at creation and
re-authorises at download. It cannot include rows hidden by RLS/resource access.

Supported MVP formats:

- GeoJSON
- CSV
- report
- catalogue
- authorised raster where explicitly permitted

## 23. API keys

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/api-keys | List safe key metadata |
| POST | /api/v1/api-keys | Create scoped key, reveal secret once |
| DELETE | /api/v1/api-keys/{id} | Revoke key |

API keys:

- belong to one organisation and accountable user
- store only a hash and visible prefix
- have allow-listed scopes
- may expire
- are revocable
- update safe last-used metadata
- never represent Airflow service identity

## 24. Webhook contract conflict

The roadmap places webhook management in Phase 4, while the approved product
specification explicitly defers signed outgoing webhooks to a later increment.

Proposed resolution:

- Do not deliver outgoing webhooks in Phase 4.
- Reserve the future resource contract and scope names.
- Implement webhook persistence/routes only when delivery, signing, replay
  protection, secret rotation, SSRF controls, and audit behaviour are delivered
  together in the later notification/integration phase.

This avoids exposing a configuration API for a capability that does not safely
operate yet.

## 25. Administration and operations

Organisation administration:

| Method | Path | Purpose |
|---|---|---|
| GET | /api/v1/admin/jobs/failed | Failed/stale jobs in organisation |
| POST | /api/v1/admin/jobs/{id}/reprocess | Audited reprocessing request |
| GET | /api/v1/admin/usage | Organisation usage/quota projection |
| GET | /api/v1/admin/audit-events | Privileged audit query |
| GET | /api/v1/admin/retention-holds | List holds |
| POST | /api/v1/admin/retention-holds | Create authorised hold |
| POST | /api/v1/admin/retention-holds/{id}/release | Release hold |

Platform operations are outside the organisation API and must not provide
routine content access. Future exceptional access requires separate operator
identity, approval, expiry, reason, and complete auditing.

## 26. OpenAPI and generated contracts

FastAPI publishes:

~~~text
GET /openapi.json
GET /docs
~~~

Contract rules:

- Every request/response has an explicit Pydantic model.
- Every enum is named and reused.
- Every route documents role/access requirements.
- Mutation routes include idempotency/concurrency examples.
- Spatial schemas use GeoJSON-compatible definitions.
- Error responses use the shared problem schema.
- Internal routes are excluded from the public generated client.

The public OpenAPI document is saved during CI and compared for breaking
changes. A generated typed client package may be published from that document,
but no client application implementation is part of this phase design.

Versioning rules:

- Additive optional fields are allowed within v1.
- Removing/renaming fields or changing meaning requires v2 or a documented
  compatibility window.
- Database migration versions and HTTP API versions are separate concepts.

## 27. Database additions required for Phase 4

The Phase 3 schema covers the core domain, but Phase 4 requires a new Alembic
revision for:

- tags and site_tags
- saved_filters
- idempotency_records
- local_asset_grants
- API service identities/credentials
- job lease fields or a job_leases table
- notification_preferences
- version columns on mutable resources
- optional safe full-text/trigram indexes
- quota configuration/usage projections

Webhook tables are excluded unless the product owner chooses to override the
approved deferral.

The migration must add RLS to every new organisation-owned table, tenant-aware
foreign keys, status/expiry indexes, and rollback tests.

## 28. Service/module design

Proposed API package structure:

~~~text
app/
  main.py
  settings.py
  api/
    dependencies.py
    errors.py
    pagination.py
    middleware.py
    v1/
      router.py
      auth.py
      organisation.py
      memberships.py
      sites.py
      schedules.py
      observations.py
      events.py
      jobs.py
      assets.py
      notifications.py
      exports.py
      admin.py
    internal/
      router.py
      jobs.py
      catalogue.py
  domain/
    services/
    state_machines/
    permissions/
  repositories/
  schemas/
  storage/
  security/
  db/
~~~

Routes validate HTTP input and translate domain results. Domain services own
business rules and state transitions. Repositories own SQL. Route handlers must
not contain raw multi-table workflow logic.

## 29. Implementation slices

### Slice 4.1: API foundation and authentication

- Configuration validation and secret safety checks.
- Structured logging and request IDs.
- Shared problem responses.
- v1 router.
- Bearer verification and request context.
- Login, refresh, logout, reset, invitation acceptance, and /me.
- Authentication rate limiting.
- Contract/security tests.

Acceptance: an invited user can create a session through OpenAPI, refresh it,
inspect /me, log out, and immediately lose access.

### Slice 4.2: Organisation and membership administration

- Organisation, department, team, member, and invitation endpoints.
- Role/status transitions and last-owner protection.
- Team/department consistency.
- Audit queries.

Acceptance: an administrator can invite and manage members while analysts and
cross-tenant users are denied.

### Slice 4.3: Sites, boundaries, grids, and schedules

- Phase 4 schema additions for tags, filters, and resource versions.
- Site CRUD and soft deletion.
- AOI validation.
- Immutable boundary/grid versions.
- Spatial queries.
- Team grants and sensitive-site filtering.
- Schedule lifecycle and manual trigger contract.

Acceptance: an administrator can register a validated site and grid, configure
monitoring, and an authorised analyst can query only permitted cells/sites.

### Slice 4.4: Observations, events, and human review

- Observation/catalogue read APIs.
- Event queue and state machine.
- Assignments, reviews, evidence, comments, and timeline.
- Verification-officer assignment isolation.

Acceptance: a possible signal can move through remote review and authorised
referral without an automated actor performing a human-only decision.

### Slice 4.5: Jobs and worker contract

- Product job endpoints.
- Internal service authentication.
- Claim/lease/heartbeat.
- Idempotent stage/completion/failure callbacks.
- Retry/cancellation/reprocessing.

Acceptance: Airflow can execute a job through FastAPI without directly mutating
product tables, and duplicate callbacks do not duplicate results.

### Slice 4.6: Assets, notifications, exports, API keys, and administration

- Local asset-provider implementation.
- Controlled upload/download grants.
- Notification preferences/subscriptions.
- Export lifecycle.
- API-key lifecycle.
- Failed-job, quota, retention-hold, and audit administration.

Acceptance: protected outputs are accessible only through current
authorisation, and every privileged/export action is audited.

### Slice 4.7: Contract hardening

- Cursor, filter, idempotency, concurrency, and error coverage.
- OpenAPI examples.
- Stored public OpenAPI artifact.
- Generated client package.
- Full unit, integration, contract, authorisation, concurrency, and spatial
  tests.

Acceptance: the approved MVP journey works end to end through OpenAPI.

## 30. Decisions requiring confirmation

The existing approved documents leave three material choices:

1. Session transport. Confirmed and implemented: Bearer access token plus
   rotating HttpOnly refresh cookie, with explicit refresh body allowed only
   for local OpenAPI testing.
2. Phase 4 AOI import scope. Recommended: GeoJSON Polygon/MultiPolygon through
   the synchronous API; zipped Shapefile, KML, and GeoPackage become validated
   asynchronous imports after the core endpoint works.
3. Webhook conflict. Recommended: follow the product specification and defer
   persistence, secret management, and delivery together to the later
   integration phase; do not create non-functional Phase 4 webhook routes.
