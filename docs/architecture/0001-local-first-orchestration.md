# ADR 0001: Local-first platform with Airflow orchestration

- Status: Accepted
- Scope: Phase 1 architecture and local development

## Context

Nigeria Forest Monitor is being built and processed locally before any hosting
provider is selected. The product needs user-defined recurring schedules, manual
triggers, durable job state, multi-stage raster processing, retries,
reprocessing, provenance, and operator visibility.

A cron-only scheduler would be simple but would not visibly model or operate the
multi-stage geospatial pipeline. Conversely, allowing an orchestrator to become
the product database would couple user-facing state to technical execution.

## Decision

Use PostgreSQL/PostGIS as the authoritative product datastore and Apache Airflow
as the local batch-pipeline orchestrator.

```text
Browser
  |
Next.js + MapLibre
  |
FastAPI
  |---------------- PostgreSQL/PostGIS (product source of truth)
  |---------------- local raster directory (COGs)
  |---------------- TiTiler (raster tiles)
  |
  +-- trigger/query --> Airflow API
                         |
                         +-- imagery discovery DAG
                         +-- parameterised processing DAG
                         +-- reprocessing/backfill DAG
                         +-- callbacks --> FastAPI
```

All components run locally. Docker Compose is the intended integration topology;
individual services may run directly during focused development.

## Responsibility boundaries

### PostgreSQL/PostGIS

- Users, organisations, departments, teams, roles, and access rules
- Sites, boundaries, grids, and schedules
- Authoritative processing-job and user-visible status
- Catalogue references, observations, assets, and provenance
- Change events, evidence, reviews, assignments, and resolutions
- Notifications, retention, and audit records

### FastAPI

- Sole operational API for the frontend
- Authentication/authorization boundary
- Validation and domain state transitions
- Job creation, cancellation, retry, and status projection
- Airflow DAG triggering and callback authentication
- Signed/controlled raster and export access when required

### Airflow

- Technical dependency ordering for batch processing
- Scheduled imagery discovery through one coordinator DAG
- Parameterised processing by product job ID; never one generated DAG per site
- Task-level retry, logs, timing, and operator diagnostics
- Explicit historical reprocessing and backfill workflows
- Reporting technical progress and outcomes to FastAPI

Airflow never becomes the user-facing source of truth. Its UI is operator-only,
and DAG tasks do not mutate product tables directly.

### Local raster storage and TiTiler

- Source imagery remains referenced in upstream catalogues where practical.
- Derived COG files are stored in a mounted local data directory.
- PostgreSQL contains metadata and lineage, never TIFF bytes.
- TiTiler reads the mounted raster directory and serves tiles locally.

### Next.js/MapLibre

- Uses FastAPI for operational and geospatial product data.
- Never connects directly to product database tables.
- Never talks directly to Airflow.
- Displays raster tiles from the controlled local TiTiler boundary.

## Trigger flows

### Manual processing

1. An authorised user requests processing through FastAPI.
2. FastAPI creates an idempotent PostgreSQL job.
3. FastAPI triggers the parameterised Airflow DAG with the job ID.
4. Airflow tasks obtain authorised inputs through FastAPI and run the shared
   processing package.
5. Airflow reports stage progress and completion/failure through authenticated
   callbacks.
6. FastAPI validates and commits user-visible state and provenance.

### Recurring monitoring

1. A single Airflow coordinator DAG runs on a fixed internal cadence.
2. It asks FastAPI for due active schedules.
3. FastAPI evaluates schedule state and creates idempotent discovery jobs.
4. Eligible new observations create processing jobs and parameterised DAG runs.
5. Suspended schedules produce no new work.

Product-defined weekly, fortnightly, and monthly schedules remain database
records; they are not Airflow DAG definitions.

## Database separation

Airflow requires its own metadata database. Locally, it may use the same
PostgreSQL server but must use a separate database and credentials from the
application database. Airflow schema migrations never run against product
tables.

## Security constraints

- Airflow is not exposed to product users.
- DAG callbacks use a dedicated, revocable service identity.
- DAG parameters contain opaque product identifiers, not customer secrets.
- Airflow tasks receive only the access required for a job.
- Exceptional operator access remains approved, time-bound, and audited.

## Consequences

### Benefits

- Real DAG visibility, retries, reprocessing, and operator diagnostics
- PostgreSQL remains the durable product truth
- User schedules can change without generating DAG code
- Processing functions remain testable outside Airflow
- Hosting decisions do not block local development

### Costs

- Additional local services, memory, migrations, upgrades, and monitoring
- State synchronization between Airflow and FastAPI must be idempotent
- Airflow task success does not count as product completion until FastAPI commits
  outputs and provenance

## Deferred decisions

- Local authentication implementation
- Airflow executor and container topology
- Hosted frontend, API, Airflow, database, object storage, and email providers
- Production migration to Vercel, Render, Supabase, R2, or alternatives
- Monthly infrastructure budget and cost-alert thresholds
