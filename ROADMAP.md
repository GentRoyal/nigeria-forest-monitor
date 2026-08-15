# Nigeria Forest Monitor — Production Roadmap

This checklist evolves the research prototype into a deployed, multi-tenant
forest-change monitoring product. The product is the site-monitoring workflow;
remote-sensing and detection algorithms are replaceable capabilities inside it.

Work in thin vertical slices and tick an item only after its acceptance test
passes. A phase is complete only when its stated outcome works end to end.

## Product mission

Give organisations a dependable way to register places they care about,
monitor each place on a recurring schedule, inspect new satellite observations,
review possible changes, collaborate on findings, and retain an auditable
history of what changed, when, where, and how it was assessed.

This is **not** a notebook wrapped in a dashboard, an isolated ML demo, or an
automated system for declaring wrongdoing. Detection prioritises human review;
the platform, workflow, provenance, and operational reliability are the core.

## Target system

```text
Users / organisations / analysts
              |
       Next.js + MapLibre
              |
       FastAPI platform API
       /       |          |       \
PostgreSQL  Airflow    TiTiler   notifications
+ PostGIS     |           |             |
+ local auth  |      local COG storage  |
              v      + STAC metadata    |
       local Docker processing <--------+
```

Version 1 lets an organisation invite users, register monitored sites, define a
grid and monitoring schedule, discover and process new imagery, compare dated
layers, triage change events, assign and review findings, receive alerts, export
results, and inspect a complete site history.

## Product capabilities

- **Portfolio management:** organisations, roles, invitations, sites, tags,
  ownership, search, saved filters, and portfolio summaries.
- **Continuous monitoring:** per-site schedules, imagery discovery, coverage and
  quality checks, idempotent processing, retries, and freshness indicators.
- **Geospatial experience:** AOI drawing/import, grid navigation, raster layers,
  before/after comparison, timelines, and spatial/temporal filtering.
- **Analyst operations:** event queues, severity, assignment, acknowledgement,
  notes, evidence, review decisions, resolution, and false-positive feedback.
- **Communication:** in-app notifications, email/webhooks, subscriptions,
  digests, delivery history, and escalation rules.
- **Interoperability:** documented APIs, API keys, GeoJSON/CSV/report exports,
  raster downloads, STAC metadata, and reproducible provenance.
- **Platform operations:** administration, audit logs, quotas, cost controls,
  observability, backups, security, data governance, and disaster recovery.

Status: `[x]` completed, `[ ]` pending, and **Current** is the active phase.

## Phase 0 — Analytical foundation

- [x] Create root-aware, validated project configuration.
- [x] Separate ingestion, preprocessing, detection, scoring, and reporting.
- [x] Provide a shared command-line pipeline.
- [x] Combine exploratory workflows into one documented notebook.
- [x] Add offline regression tests for core behaviour.
- [x] Document model limitations and responsible-use boundaries.

**Done when:** The pipeline works outside the notebook and offline tests pass.

## Phase 1 — Product contract and architecture **Complete**

- [x] Write MVP personas, user journeys, user stories, and explicit non-goals.
- [x] Define the primary journey: onboard organisation → create site → schedule
  monitoring → discover imagery → process → review event → notify/export.
- [x] Define roles and permissions for administrator, analyst, authorised
  verification officer, executive viewer, and platform operator.
- [x] Define users, organisations, departments, teams, sites, grid cells,
  observations, raster
  assets, schedules, jobs, change events, assignments, reviews, subscriptions,
  notifications, exports, API keys, and audit records.
- [x] Define lifecycles for schedules, jobs, observations, events, assignments,
  reviews, and notifications.
- [x] Define boundaries between the frontend, API, Airflow orchestrator, tile
  service, local raster storage, and PostgreSQL/PostGIS.
- [x] Record the local-first architecture and explicitly defer Supabase, Render,
  Vercel, and R2 until deployment requirements and measured usage justify them.
- [x] Specify tenancy, security, privacy, retention, attribution, and responsible-
  use requirements.
- [x] Set measurable budgets for processing latency, map/API performance, data
  freshness, availability, accessibility, and failures; explicitly defer the
  monthly operating-cost cap for later approval.
- [x] Define an MVP demo scenario and representative seed dataset.

**Done when:** `docs/product-spec.md`, `docs/data-model.md`, and an architecture
decision record describe the complete version-1 journey, permission model,
service targets, and non-goals without depending on a particular detector.

## Phase 2 — Production repository structure **Complete**

- [x] Convert the repository to a monorepo without breaking the analytical core.
- [x] Create `apps/web` for Next.js and MapLibre.
- [x] Create `apps/api` for FastAPI.
- [x] Create `apps/orchestrator` for Airflow-based local batch orchestration.
- [x] Create `apps/tiles` for restricted local COG tile access.
- [x] Move reusable geospatial logic into `packages/forest_monitor`.
- [x] Add shared development commands and environment templates.
- [x] Add Dockerfiles and a validated local Docker Compose stack.
- [x] Add formatting, linting, type checking, and pre-commit hooks.
- [x] Add analytical regression, API, tile-security, and frontend build test lanes;
  retain contract, browser, and realistic geospatial fixtures in their feature phases.
- [x] Add architecture decision records and a runbook template.
- [x] Build all images and smoke-test the complete running stack, including an
  executed Airflow DAG.

**Done when:** One command starts local services and the worker, and existing
analytical tests still pass.

## Phase 3 — PostgreSQL/PostGIS, local authentication, and security **Complete**

- [x] Create the local PostgreSQL/PostGIS application database and migration toolchain.
- [x] Keep Airflow metadata isolated from the product database.
- [x] Create organisation, membership, site, grid-cell, observation, asset,
  schedule, job, event, assignment, review, subscription, notification, export,
  API-key, and audit-event tables.
- [x] Add spatial, temporal, status, and foreign-key indexes.
- [x] Define geometry types, coordinate systems, precision, and validity rules.
- [x] Implement PostgreSQL-backed authentication, invitations, membership lifecycle,
  password hashing, session rotation, and revocation.
- [x] Implement owner, administrator, analyst, and viewer permissions.
- [x] Implement organisation-based row-level security for every owned table and
  storage object.
- [x] Add immutable audit records for privileged and analyst actions.
- [x] Define deletion, retention, anonymisation, backup, and restore behaviour.
- [x] Generate realistic seed data.
- [x] Add migration, rollback, spatial-query, permission, and RLS tests.

**Done when:** Test organisations cannot access each other's data, and spatial
queries return the correct grid cells for a site or viewport. Role permissions,
invitations, audit history, and deletion rules are verified automatically.

## Phase 4 — FastAPI backend **In progress**

- [x] Add local configuration, structured request logging, health checks, error
  envelopes, and `/api/v1` versioning.
- [x] Verify locally issued access tokens, active sessions, organisation membership,
  and current role on every product request.
- [x] Implement organisation, department, team, membership, invitation, session,
  and profile endpoints.
- [x] Implement site CRUD, AOI validation, ownership, tags, search, immutable
  boundary versions, grid history, map-cell queries, and grid generation.
- [x] Implement monitoring schedules and lifecycle controls.
- [x] Implement observation history/detail reads and a role-filtered event queue.
- [x] Implement event reviews, transitions, assignments, evidence metadata, and
  auditable comments; verification officers see only specifically assigned events.
- [x] Implement manual jobs, privileged job visibility, idempotent retries, and
  cancellation.
- [x] Implement separate worker authentication plus claim, lease, heartbeat, stage,
  completion, and failure callbacks; workers never use interactive user credentials.
- [x] Publish catalogue items, observations, processing-run provenance, raster assets,
  and possible-change events from authenticated, lease-bound worker callbacks.
- [ ] Complete the single-transaction publication path for a processing result:
  processing run, assets, grid measurements, and change events must commit together
  before the job can become completed.
- [ ] Add a unified site timeline that combines observations, jobs, events, reviews,
  assignments, and audit entries.
- [ ] Issue controlled local asset upload/download references, preserving an interface
  that can later support signed object-storage URLs.
- [x] Implement recipient-scoped in-app notification reads, per-user notification
  preferences, and authorised site/event subscriptions.
- [x] Implement deduplicated, preference-aware notification fan-out for detected
  change events and new assignments, with in-app/email local outbox records.
- [ ] Implement external email sending, scheduled digest dispatch, exports, API
  keys, and webhook management.
- [x] Add the privileged operational audit query.
- [ ] Add administration endpoints for failed jobs, reprocessing, and quotas.
- [x] Add signed cursor pagination, idempotency keys, optimistic concurrency, and
  consistent errors to the completed resource slices.
- [ ] Add the remaining spatial/temporal filters and OpenAPI examples.
- [ ] Version API contracts and publish a generated client for the frontend.
- [x] Add unit, integration, contract, authorization, RLS, concurrency, and spatial
  regression coverage for the completed slices.
- [ ] Add end-to-end worker-callback and full human-review workflow coverage.

**Done when:** The MVP workflow works through OpenAPI with authentication and
organisation isolation, stable contracts, idempotent writes, and auditability.

## Phase 5 — Continuous monitoring and processing worker

- [ ] Package the existing pipeline as an idempotent worker task.
- [ ] Create per-site cadence, sensor, cloud-cover, baseline, and alert settings.
- [x] Add a fixed-cadence Airflow coordinator that creates duplicate-safe due
  discovery jobs through the scheduler-only API.
- [x] Add a parameterised Airflow STAC discovery DAG for the approved Planetary
  Computer Sentinel-2 catalogue; it registers candidate source items through
  lease-bound API callbacks.
- [ ] Record per-schedule discovery cursors and use them for incremental rather
  than fixed-window discovery.
- [ ] Check AOI coverage, acquisition geometry, data quality, and duplicates before
  processing.
- [ ] Expose site freshness, next scheduled run, last successful observation, and
  monitoring health.
- [x] Claim jobs and report lease-protected heartbeat, progress, stage, failure, and
  completion callbacks.
- [x] Query free Sentinel-2 L2A candidates through the approved Planetary Computer
  STAC API for an authorised site AOI.
- [ ] Add Sentinel-1 and Landsat providers, asset signing, provider fallback, and
  source-specific quality filtering.
- [ ] Stream only the data needed for the selected AOI.
- [ ] Run quality masking, preprocessing, and change detection.
- [ ] Produce analysis-ready Cloud Optimized GeoTIFFs with overviews.
- [ ] Calculate grid statistics and detected change events.
- [ ] Upload outputs with signed URLs and register metadata in PostGIS.
- [ ] Store provenance, parameters, checksums, and model version.
- [ ] Add retry, timeout, cancellation, and abandoned-job recovery.
- [ ] Support safe reprocessing with a new algorithm/configuration version while
  retaining prior results and provenance.
- [ ] Add worker concurrency, resource, disk, bandwidth, and cost limits.
- [ ] Add a documented offline-worker mode and visible stale/offline behaviour.
- [ ] Add fixture-based end-to-end tests without live service dependencies.

**Done when:** A recurring schedule discovers a new observation, runs exactly
once in a clean worker container, publishes reproducible results, updates site
freshness, and recovers safely from interruption.

## Phase 6 — Raster catalogue, storage, and tiles

- [ ] Create development and production object-storage buckets.
- [ ] Define deterministic object keys and retention rules.
- [ ] Validate uploaded rasters as COGs.
- [ ] Represent source and derived assets as STAC Items and Collections.
- [ ] Track source licence, attribution, lineage, quality, processing version,
  supersession, and deletion state for every asset.
- [ ] Deploy TiTiler with controlled object-storage access.
- [ ] Add RGB, false-colour, SAR, change-score, and mask styles.
- [ ] Add tile caching, cache headers, thumbnails, and previews.
- [ ] Add signed private access, public-demo isolation, and storage access logs.
- [ ] Define hot/cold retention, cleanup, orphan detection, and quota enforcement.
- [ ] Test byte-range reads and map performance with realistic rasters.

**Done when:** The map requests tiles without proxying TIFF bytes through
FastAPI, and each dated asset is traceable through the catalogue.

## Phase 7 — Frontend monitoring application

- [ ] Build onboarding, authentication, invitations, profile, role, and
  organisation selection flows.
- [ ] Build the monitored-sites dashboard with search, tags, saved filters,
  freshness, health, and portfolio summaries.
- [ ] Create sites by map drawing or GeoJSON upload.
- [ ] Render site boundaries and selectable grid cells with MapLibre.
- [ ] Add imagery date/layer controls and before/after comparison.
- [ ] Add swipe/opacity comparison, legends, coordinates, scale, and shareable map
  state.
- [ ] Add the site timeline and event queue/detail workspace.
- [ ] Show job progress, offline-worker state, failures, and retries.
- [ ] Add event severity, assignment, acknowledgement, evidence, notes, review,
  resolution, and false-positive feedback.
- [ ] Build organisation settings, team management, monitoring settings,
  notification preferences, API keys, and audit views.
- [ ] Build an operator view for schedules, queues, failed jobs, storage, and
  reprocessing.
- [ ] Add responsive, accessible loading, empty, and error states.
- [ ] Meet explicit browser, mobile, keyboard, contrast, and performance budgets.
- [ ] Add unit, component, and browser end-to-end tests.

**Done when:** A user can create a site, request processing, track the job,
compare layers, triage and resolve an event, export it, and inspect its audit
history without using the notebook or database console.

## Phase 8 — Alerts, collaboration, exports, and integrations

- [x] Add recipient-scoped in-app notifications, unread filtering, and idempotent
  read state.
- [x] Add local email-outbox delivery records that respect user channel preferences.
- [ ] Add external email-provider delivery and scheduled digest dispatch.
- [x] Add per-user site/event subscriptions and notification channel/digest
  preferences.
- [x] Add notification deduplication and delivery-history records.
- [ ] Add severity thresholds, quiet hours, escalation, and delivery retries.
- [ ] Add signed outgoing webhooks with replay protection and a test action.
- [x] Add event assignment, acceptance/decline/cancellation, comments, and
  resolution transitions to the API.
- [ ] Add mentions and resolution service-level targets.
- [ ] Export sites, grids, observations, and events as GeoJSON and CSV.
- [ ] Generate a shareable PDF/HTML site or event report with provenance.
- [ ] Permit authorized COG downloads and expose STAC/API access.
- [ ] Add scoped, revocable, expiring API keys with usage logs.
- [ ] Test notification failure, webhook replay, permission boundaries, exports,
  and large result sets.

**Done when:** A detected event reaches the right subscriber, can be assigned and
resolved, exports with its evidence/provenance, and leaves an auditable delivery
and review trail.

## Phase 9 — Deployment, environments, and CI/CD

- [ ] Define infrastructure as code for reproducible cloud resources and config.
- [ ] Deploy the frontend to Vercel with previews.
- [ ] Deploy FastAPI and TiTiler as separate Render services.
- [ ] Configure Supabase production migrations and backups.
- [ ] Configure R2/S3 CORS, lifecycle rules, and secrets.
- [ ] Add CI for Python and TypeScript linting, tests, builds, and migrations.
- [ ] Build and scan containers automatically.
- [ ] Separate local, test, preview, staging, and production environments.
- [ ] Add backward-compatible migration checks and deployment smoke tests.
- [ ] Add feature flags, release promotion, rollback, and database rollback/forward
  procedures.
- [ ] Pin runtime/tool versions and generate software bills of materials.
- [ ] Document environment provisioning, secret rotation, rollback, and disaster
  recovery.

**Done when:** A release merge passes CI and creates a repeatable deployment
with no secrets committed to the repository.

## Phase 10 — Reliability, security, governance, and cost

- [ ] Add error tracking, correlation/request IDs, audit logs, uptime checks, and
  synthetic monitoring of the primary user journey.
- [ ] Track API/tile latency, queue depth, worker health, job duration, failures,
  notification delivery, storage, and data freshness.
- [ ] Define service-level indicators, objectives, alerts, and error budgets.
- [ ] Add rate limits, payload limits, secure headers, and dependency scanning.
- [ ] Add least-privilege service identities, secret rotation, encryption, and
  privileged-operation controls.
- [ ] Threat-model authentication, tenancy, signed URLs, uploads, webhooks, tiles,
  and worker claims.
- [ ] Load-test APIs, spatial queries, job claiming, and raster tiles.
- [ ] Rehearse database backup and restore.
- [ ] Write operator, incident-response, and local-worker runbooks.
- [ ] Add per-organisation quotas, usage metering, storage/compute budgets, cost
  dashboards, and runaway-job protection.
- [ ] Verify dataset licences, required attribution, retention, deletion, privacy,
  and data-residency expectations.
- [ ] Complete accessibility, privacy, security, and responsible-use reviews.
- [ ] Run a production-readiness review and remediate all release blockers.

**Done when:** Failures are detectable and recoverable, access is auditable,
backups are proven, costs are bounded, governance requirements are documented,
and service targets are met.

## Phase 11 — Validation, adoption, and public demonstration

- [ ] Define an evaluation dataset with reviewed labels.
- [ ] Report precision, recall, false-positive patterns, and geographic limits.
- [ ] Report area monitored, changes reviewed, and analyst time saved without
  presenting detections as confirmed wrongdoing.
- [ ] Publish architecture, lineage, model card, and limitations pages.
- [ ] Add a safe public demo dataset.
- [ ] Add contextual onboarding, user documentation, API documentation, and
  operator documentation.
- [ ] Run usability sessions with representative users and record improvements.
- [ ] Measure onboarding completion, active monitored sites, freshness, event
  review time, notification delivery, retention, and support burden.
- [ ] Record a complete product walkthrough and cloud-migration story.
- [ ] Use measured demand to choose Render workers, AWS Batch, or another managed
  batch service.

**Done when:** The deployed system demonstrates engineering depth and measurable
usefulness with honest, reproducible evidence.

## Release gates

### Internal alpha

- [ ] One organisation can create a site and complete one manual processing job.
- [ ] A raster and grid statistics appear on the map with traceable provenance.
- [ ] An analyst can review the resulting event end to end.

### Private beta

- [ ] Multiple isolated organisations can run recurring monitoring.
- [ ] Alerts, assignments, exports, audit logs, quotas, and operator recovery work.
- [ ] Staging, CI/CD, monitoring, backups, security, and support runbooks are live.

### Public demonstration

- [ ] A safe seeded dataset demonstrates the complete workflow without secrets or
  sensitive operational data.
- [ ] Performance, reliability, accessibility, cost, and responsible-use gates
  pass with published limitations.
- [ ] The system operates without notebooks or direct database intervention.

## Immediate delivery queue

1. [x] Write the MVP product specification and non-goals.
2. [x] Document roles, the end-to-end journey, and release gates.
3. [x] Design the PostGIS model and job/event/alert state machines.
4. [x] Restructure the repository into the production monorepo.
5. [x] Record the local-first architecture and defer hosting providers.
6. [x] Preserve and rerun analytical tests after restructuring.
7. [ ] Deliver the internal-alpha vertical slice before broad feature expansion.
