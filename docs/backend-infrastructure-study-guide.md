# Nigeria Forest Monitor: Backend and Infrastructure Study Guide

This document explains the backend, database, geospatial, orchestration,
security, testing, Git, and Docker work completed so far.

The goal is not merely to list commands. It explains what each tool does, why we
used it, how the pieces communicate, and how to derive the right command from a
failed log during an interview or real incident.

## 1. What we have built

The current backend platform contains:

- A FastAPI application as the product API boundary.
- PostgreSQL with PostGIS as the authoritative product datastore.
- Alembic for versioned and reversible database migrations.
- PostgreSQL-backed authentication and authorisation services.
- Forced organisation-level row-level security for tenant isolation.
- Immutable audit records.
- A restricted TiTiler service for local Cloud Optimized GeoTIFFs.
- Apache Airflow for local batch-workflow orchestration.
- A shared Python package containing the analytical code that used to live
  primarily in notebooks.
- Dockerfiles for the API, tile service, and Airflow.
- Docker Compose for the local multi-service environment.
- Unit, integration, migration, RLS, spatial, and security tests.
- Database backup and restore scripts.

The backend-only flow is:

~~~text
Authorised client
      |
      v
FastAPI API ----------------------> PostgreSQL/PostGIS
      |                                  ^
      |                                  |
      +---- triggers/receives ---------- Airflow
                                         |
                                         v
                               shared analysis package

Restricted TiTiler <------------- local COG raster directory
~~~

Important rule: PostgreSQL is the product source of truth. Airflow knows how a
workflow is executing, but product-visible job state belongs in PostgreSQL.
Raster bytes are stored as files or objects; PostgreSQL stores their metadata,
lineage, checksums, bounds, and object keys.

## 2. Tool inventory

| Tool | What it does here | Why it was selected |
|---|---|---|
| Python 3.11 | Runs API, tests, analysis, migrations, and utilities | Stable shared runtime across services |
| FastAPI | Defines HTTP API and health endpoints | Typed async Python API with OpenAPI documentation |
| Uvicorn | Runs the ASGI FastAPI applications | Lightweight production-compatible ASGI server |
| PostgreSQL 16 | Stores product, identity, workflow, and audit data | Transactions, constraints, JSONB, RLS, mature operations |
| PostGIS 3.4 | Adds geometry types and spatial queries | Required for sites, grids, viewports, footprints, and events |
| Psycopg 3 | Connects Python to PostgreSQL | Modern sync/async PostgreSQL driver |
| Alembic | Versions schema changes | Repeatable upgrade and rollback history |
| Argon2id | Hashes user passwords | Memory-hard password hashing |
| PyJWT | Issues/verifies short-lived access tokens | Stateless access token verification |
| Apache Airflow | Schedules and orchestrates processing jobs | Visibility, retries, DAG execution, operational history |
| TiTiler | Serves raster tiles from COGs | Standard geospatial tiling without building a tiler from scratch |
| Docker | Packages each service and its dependencies | Reproducible runtime independent of host setup |
| Docker Compose | Runs the local multi-container stack | Networking, dependencies, ports, volumes, and health checks |
| Pytest | Runs unit and PostgreSQL integration tests | Fixtures, assertions, parametrisation, markers |
| Ruff | Lints and formats Python | Fast consistent static checks |
| PowerShell | Local Windows automation | Matches the current development machine |
| Git/GitHub | Version control and remote collaboration | Ordered, reviewable, reversible change history |

## 3. Relevant repository structure

~~~text
apps/
  api/
    Dockerfile
    alembic.ini
    alembic/
      env.py
      versions/0001_phase3_foundation.py
    app/
      main.py
      database.py
      db/
      security/
    tests/
  orchestrator/
    Dockerfile
    dags/system_smoke.py
  tiles/
    Dockerfile
    app/main.py
    tests/

packages/
  forest_monitor/
    src/forest_monitor/

infra/
  postgres/
    init/001-create-databases.sql
    ensure-databases.sh

scripts/
  bootstrap.ps1
  dev.ps1
  check.ps1
  backup-database.ps1
  restore-database.ps1

compose.yaml
pyproject.toml
.env.example
.gitignore
~~~

## 4. Local setup

### Prerequisites

- Python 3.11
- Docker Desktop with Docker Compose
- PowerShell
- Git

### Initial setup

~~~powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
~~~

The bootstrap script:

1. Creates .venv when it does not exist.
2. Installs the shared analytical requirements.
3. Installs the repository as an editable Python package.
4. Installs API and tile-service development dependencies.

Start only the backend services:

~~~powershell
docker compose up --build postgres postgres-init api tiles airflow
~~~

Detached mode returns control to the terminal:

~~~powershell
docker compose up -d --build postgres postgres-init api tiles airflow
~~~

Check services:

~~~powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
Invoke-RestMethod http://localhost:8001/health/live
~~~

## 5. Docker fundamentals you should be able to explain

### Image

An image is an immutable package containing the application, runtime,
dependencies, and default startup command. A Dockerfile builds an image.

### Container

A container is a running instance of an image. Removing a container does not
necessarily remove persisted data because data may live in a volume.

### Volume

A volume stores data independently of a container lifecycle. Our named
postgres_data volume preserves the PostgreSQL cluster across container
recreation. The airflow_logs volume preserves Airflow logs.

### Bind mount

A bind mount maps a host path into a container. Examples:

- Airflow DAGs are mounted read-only into /opt/airflow/dags.
- The local raster directory is mounted read-only into /data/rasters.
- PostgreSQL init scripts are mounted into /docker-entrypoint-initdb.d.

### Docker network and service names

Compose creates a project network and internal DNS. Containers use Compose
service names as hosts:

- API connects to postgres:5432.
- Airflow connects to postgres:5432.
- postgres-init connects to postgres.

From the Windows host, the project database is localhost:5433. This distinction
is critical:

~~~text
Host command:       localhost:5433
Container command:  postgres:5432
~~~

We deliberately use host port 5433 because a native PostgreSQL installation was
already listening on localhost:5432.

## 6. Reading the API Dockerfile

The API Dockerfile is:

~~~dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY apps/api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/app ./app
COPY apps/api/alembic.ini ./alembic.ini
COPY apps/api/alembic ./alembic

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m app.db.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
~~~

Line-by-line:

1. FROM selects a small Python 3.11 base image.
2. PYTHONDONTWRITEBYTECODE avoids unnecessary .pyc files.
3. PYTHONUNBUFFERED makes logs appear immediately.
4. WORKDIR makes /app the default directory.
5. Requirements are copied before source code so Docker can cache dependency
   installation when only source files change.
6. RUN installs dependencies while building the image.
7. COPY adds the API and migration files to the image.
8. EXPOSE documents the container port; it does not publish the port by itself.
9. CMD runs migrations, runs the idempotent seed, and starts Uvicorn.
10. The double ampersand means the next command runs only if the preceding
    command succeeds. A failed migration therefore prevents the API from
    starting with an incompatible schema.

Build only the API image:

~~~powershell
docker compose build api
~~~

Rebuild without cached layers when dependency caching is suspected:

~~~powershell
docker compose build --no-cache api
~~~

Start or recreate only the API and its required dependencies:

~~~powershell
docker compose up -d --build api
~~~

## 7. Understanding Docker Compose

Docker Compose describes the local system rather than one container.

### PostgreSQL service

~~~yaml
postgres:
  image: postgis/postgis:16-3.4
  ports:
    - "5433:5432"
  volumes:
    - postgres_data:/var/lib/postgresql/data
    - ./infra/postgres/init:/docker-entrypoint-initdb.d:ro
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U postgres -d postgres"]
~~~

The left port is the host port. The right port is the container port.

~~~text
5433:5432
 ^    ^
 |    container
 host
~~~

The health check does not prove every application table exists. It proves the
PostgreSQL server is accepting connections.

### Dependency conditions

The API depends on postgres-init:

~~~yaml
depends_on:
  postgres-init:
    condition: service_completed_successfully
~~~

postgres-init depends on PostgreSQL health:

~~~yaml
depends_on:
  postgres:
    condition: service_healthy
~~~

The resulting order is:

~~~text
PostgreSQL becomes healthy
        ->
idempotent database provisioning completes
        ->
API migration and seed run
        ->
Uvicorn starts
~~~

This is stronger than merely starting containers in a written order.

### Health versus readiness

The API exposes:

- /health/live: the process is alive.
- /health/ready: the API can connect to PostgreSQL.

Liveness answers: should the platform restart this process?

Readiness answers: should the platform send traffic to this process?

## 8. Docker command cheat sheet

The usual Compose command shape is:

~~~text
docker compose <command> [options] [service] [service command]
~~~

Examples:

~~~text
docker compose logs --tail=100 api
               ^command ^option   ^service

docker compose exec -T postgres psql -U postgres -d forest_monitor
               ^command  ^service ^command executed inside the container
~~~

Use logs to read service output, exec to run inside an existing container, run
for a new one-off container, build to create an image, and up to converge the
declared services into a running state.

Validate Compose syntax and interpolation:

~~~powershell
docker compose config
docker compose config --quiet
~~~

List service state, ports, and health:

~~~powershell
docker compose ps
docker compose ps api
~~~

Start services:

~~~powershell
docker compose up postgres postgres-init api tiles airflow
docker compose up -d postgres postgres-init api tiles airflow
~~~

Build and start:

~~~powershell
docker compose up -d --build api
~~~

View logs:

~~~powershell
docker compose logs api
docker compose logs --tail=100 api
docker compose logs -f api
docker compose logs --since=10m postgres
~~~

Run a command in an already-running container:

~~~powershell
docker compose exec api alembic current
docker compose exec -T postgres psql -U postgres -d postgres -c "SELECT version();"
~~~

The -T option disables pseudo-terminal allocation. It is useful in scripts and
pipelines.

Run a one-off container:

~~~powershell
docker compose run --rm api python -m app.db.seed
~~~

Restart one service:

~~~powershell
docker compose restart api
~~~

Recreate a service:

~~~powershell
docker compose up -d --force-recreate postgres
~~~

Stop without removing:

~~~powershell
docker compose stop
~~~

Stop and remove containers/network while preserving named volumes:

~~~powershell
docker compose down
~~~

Delete containers and named volumes:

~~~powershell
docker compose down -v
~~~

The last command destroys the local PostgreSQL data. Use it only when data loss
is intentional.

Inspect low-level container configuration:

~~~powershell
docker inspect nigeria-forest-monitor-api-1
~~~

Inspect networks and volumes:

~~~powershell
docker network ls
docker volume ls
docker volume inspect nigeria-forest-monitor_postgres_data
~~~

## 9. Turning a failed log into a Docker command

This is the interview skill you were tested on. Use this repeatable method.

### Step 1: Identify the failing layer

Ask:

- Is the image failing to build?
- Is the container exiting during startup?
- Is it running but unhealthy?
- Is the application healthy but unable to reach a dependency?
- Is the host reaching the wrong port or service?

### Step 2: Identify the service name

Use:

~~~powershell
docker compose ps
~~~

Prefer Compose service names such as api, postgres, postgres-init, airflow, and
tiles. Container names are less portable.

### Step 3: Read the relevant log

~~~powershell
docker compose logs --tail=100 api
~~~

If the failure is live:

~~~powershell
docker compose logs -f api
~~~

### Step 4: Translate the error into a hypothesis

Examples:

| Log phrase | Likely hypothesis |
|---|---|
| connection refused | Dependency is down, wrong host, wrong port, or not ready |
| role does not exist | Wrong database instance or provisioning did not run |
| database does not exist | Initialisation missing or wrong database name |
| password authentication failed | Wrong credential/environment variable |
| address already in use | Host port collision |
| module not found | Dependency/image build or Python path problem |
| permission denied | Filesystem permissions, RLS, role, or mounted path mode |
| migration revision not found | Image and database migration history disagree |
| unhealthy | Health-check command is failing; inspect its dependency |
| no such file | Wrong path, missing COPY, or missing bind mount |

### Step 5: Inspect rather than immediately deleting

Useful read-only commands:

~~~powershell
docker compose ps
docker compose logs --tail=100 SERVICE
docker compose config
docker compose exec SERVICE env
docker inspect CONTAINER
~~~

Do not jump straight to docker compose down -v. That may hide the root cause by
deleting the evidence and database.

### Step 6: Apply the smallest targeted action

- Configuration changed: recreate the affected service.
- Dockerfile/dependency changed: rebuild the affected image.
- Process temporarily stuck: restart the service.
- Migration missing: run the migration.
- Database role missing: run the idempotent provisioner.
- Host port occupied: change the host-side port mapping.

### Step 7: Verify the result

~~~powershell
docker compose ps SERVICE
docker compose logs --tail=100 SERVICE
Invoke-RestMethod http://localhost:PORT/health/ready
~~~

## 10. Failures we actually encountered and how we solved them

These examples are especially useful in interviews because they connect a real
log message to investigation, command selection, and a permanent fix.

### Failure A: role forest_monitor does not exist

Observed error:

~~~text
FATAL: role "forest_monitor" does not exist
~~~

Initial hypothesis: the product role was never created.

First checks:

~~~powershell
docker compose ps postgres
docker compose logs --tail=100 postgres
docker compose exec -T postgres psql -U postgres -d postgres -Atc "SELECT rolname FROM pg_roles ORDER BY rolname"
~~~

The container showed that forest_monitor did exist. That contradicted the host
error, so the next hypothesis was that the host and container commands were
reaching different PostgreSQL servers.

Check which host process owns port 5432:

~~~powershell
Get-NetTCPConnection -LocalPort 5432 -State Listen
Get-Process -Id (Get-NetTCPConnection -LocalPort 5432 -State Listen).OwningProcess
~~~

Check the Compose-published port:

~~~powershell
docker compose ps postgres
docker compose port postgres 5432
~~~

Root cause: a native PostgreSQL 17 instance was already available through
localhost:5432, while the project expected the PostGIS container. The host-side
migration command connected to the native instance.

Permanent fix:

~~~yaml
ports:
  - "5433:5432"
~~~

Host tools now use:

~~~text
postgresql://forest_monitor:forest_monitor@localhost:5433/forest_monitor
~~~

Containers still use:

~~~text
postgresql://forest_monitor:forest_monitor@postgres:5432/forest_monitor
~~~

Interview lesson: when database contents contradict each other, verify the
actual server, port, version, and network path before changing credentials.

### Failure B: old PostgreSQL volume did not run new init scripts

Docker's /docker-entrypoint-initdb.d scripts run only when the PostgreSQL data
directory is first initialised. Adding a new SQL file does not rerun it against
an existing named volume.

Deleting the volume would recreate everything, but it would also destroy data.
We instead added an idempotent postgres-init service.

Core shell logic:

~~~sh
if ! psql --host=postgres --username=postgres --dbname=postgres \
  --tuples-only --no-align \
  --command "SELECT 1 FROM pg_roles WHERE rolname='forest_monitor'" |
  grep -q 1
then
  psql --host=postgres --username=postgres --dbname=postgres \
    --command "CREATE ROLE forest_monitor LOGIN PASSWORD 'forest_monitor'"
fi
~~~

Idempotent means the command can run repeatedly and produces the same intended
state without duplicating or corrupting resources.

Run the provisioner:

~~~powershell
docker compose up --force-recreate postgres-init
~~~

Verify:

~~~powershell
docker compose exec -T postgres psql -U postgres -d postgres -Atc "SELECT rolname FROM pg_roles WHERE rolname IN ('forest_monitor','airflow')"
~~~

### Failure C: Linux shell script had Windows line endings

The Linux helper container received a shell script from a Windows filesystem.
Carriage-return characters can break shell parsing.

The Compose command normalises the mounted file before executing it:

~~~yaml
command:
  - sh
  - -c
  - "tr -d '\\015' < /opt/nfm/ensure-databases.sh > /tmp/ensure-databases.sh && sh /tmp/ensure-databases.sh"
~~~

Interview lesson: a script may look correct in an editor but fail inside Linux
because of CRLF line endings, executable permissions, or an invalid shebang.

Useful checks:

~~~powershell
docker compose logs postgres-init
docker compose run --rm postgres-init sh -n /opt/nfm/ensure-databases.sh
~~~

### Failure D: readiness request failed immediately after container start

Observed behaviour: the image built and the container started, but the first
readiness request failed while Compose reported health: starting.

Diagnostic commands:

~~~powershell
docker compose ps api
docker compose logs --tail=100 api
~~~

The logs showed that Alembic and the seed were still running before Uvicorn
became ready.

Correct response: wait for health, then verify again. Do not rebuild a healthy
image merely because the first request raced startup.

~~~powershell
docker compose ps api
Invoke-RestMethod http://localhost:8000/health/ready
~~~

### Failure E: Psycopg async connection on Windows

Observed error:

~~~text
Psycopg cannot use the ProactorEventLoop
~~~

Cause: Psycopg's async driver on Windows requires the selector event-loop
policy.

Fix:

~~~python
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
~~~

This is a Python runtime issue, not a Docker networking issue. Correctly
classifying the layer prevents random container changes.

### Failure F: Alembic migration round-trip test

Our first rollback test tried to isolate migrations through a custom PostgreSQL
search path. Alembic saw the existing public migration table and did not create
the application tables where the test expected them.

Permanent test design: create a disposable database, apply all migrations,
assert application tables exist, downgrade to base, assert they are removed,
then drop only the disposable database.

This tests real upgrade and rollback behaviour without downgrading the
developer's database.

## 11. PostgreSQL and PostGIS design

### Why PostgreSQL for all application data

The project currently keeps authentication, authorisation, spatial metadata,
workflow state, audit history, and domain records in PostgreSQL. This gives us:

- ACID transactions.
- Foreign-key and check constraints.
- Unique and partial indexes.
- JSONB for structured variable metadata.
- PostGIS spatial types and indexes.
- Row-level security.
- One backup/restore system for authoritative metadata.

### Why Airflow has a separate database

The local PostgreSQL server contains two databases and two application roles:

~~~text
forest_monitor database -> product data
airflow database        -> Airflow metadata
~~~

Airflow migrations must never run against the product database. Airflow task
retries and internal tables are implementation details, not customer-visible
product state.

### PostGIS extension

PostGIS is enabled with:

~~~sql
CREATE EXTENSION IF NOT EXISTS postgis;
~~~

It adds types and functions such as:

- geometry(Polygon, 4326)
- geometry(MultiPolygon, 4326)
- ST_IsValid
- ST_IsEmpty
- ST_SRID
- ST_Intersects
- ST_MakeEnvelope

EPSG:4326 stores longitude/latitude coordinates. Metric calculations should use
an appropriate projected CRS or geography conversion rather than assuming
degrees are metres.

### Geometry constraints

Example:

~~~sql
geometry geometry(MultiPolygon, 4326) NOT NULL,
CHECK (
  ST_IsValid(geometry)
  AND NOT ST_IsEmpty(geometry)
  AND ST_SRID(geometry) = 4326
)
~~~

This prevents invalid, empty, or incorrectly labelled geometries from entering
the database.

### Spatial index

~~~sql
CREATE INDEX boundary_geometry_gix
ON site_boundary_versions
USING gist (geometry);
~~~

A GiST index allows PostgreSQL/PostGIS to reduce the candidate geometries before
performing an exact spatial predicate.

Example viewport query:

~~~sql
SELECT cell_key
FROM grid_cells
WHERE grid_version_id = $1
  AND ST_Intersects(
    geometry,
    ST_MakeEnvelope($2, $3, $4, $5, 4326)
  );
~~~

### Tenant-consistent foreign keys

Many tables include both id and organisation_id in unique keys:

~~~sql
UNIQUE (organisation_id, id)
~~~

Child tables reference both values:

~~~sql
FOREIGN KEY (organisation_id, site_id)
REFERENCES sites (organisation_id, id)
~~~

This prevents an application bug from connecting a tenant A record to a tenant
B record.

### Row-level security

Each request transaction sets the current organisation:

~~~python
await connection.execute(
    "SELECT set_config('app.current_organisation_id', %s, true)",
    (str(organisation_id),),
)
~~~

The database policy is equivalent to:

~~~sql
CREATE POLICY tenant_isolation ON sites
USING (organisation_id = app_current_organisation_id())
WITH CHECK (organisation_id = app_current_organisation_id());
~~~

We also use:

~~~sql
ALTER TABLE sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE sites FORCE ROW LEVEL SECURITY;
~~~

FORCE makes the protection apply even to the application table owner. Without a
tenant context, tenant-owned rows are not visible.

RLS is defence in depth. The API must still perform role and resource
authorisation.

## 12. Alembic migration workflow

Alembic records the current revision in alembic_version.

Show the applied revision:

~~~powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini current
~~~

Show migration history:

~~~powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini history
~~~

Apply all pending migrations:

~~~powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini upgrade head
~~~

Downgrade one revision:

~~~powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini downgrade -1
~~~

Do not downgrade a shared or production database casually. Back up first and
understand whether the downgrade deletes tables or data.

Create a future migration:

~~~powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini revision -m "describe the schema change"
~~~

Migration principles:

1. Make schema changes repeatable.
2. Prefer transactional DDL when supported.
3. Define an honest downgrade or explicitly document irreversibility.
4. Test upgrade and rollback in a disposable database.
5. Never edit an already-deployed migration casually; add a new revision.
6. Back up before destructive data migrations.

## 13. Seed data

The seed command is:

~~~powershell
.venv\Scripts\python.exe -m apps.api.app.db.seed
~~~

Inside the API container:

~~~sh
python -m app.db.seed
~~~

The seed is idempotent because inserts use stable UUIDs and conflict handling.
It creates:

- A local government-style organisation.
- A monitoring department.
- A local owner.
- A remote-analysis team.
- Old Oyo National Park.
- Kainji Lake National Park.
- The Old Oyo-Kwara-Kainji monitoring corridor.

It does not invent approximate protected-area boundary polygons. Verified
boundary provenance is required before real predefined geometries are imported.

## 14. FastAPI work

The API application currently provides:

~~~text
GET /health/live
GET /health/ready
GET /api/v1/system/info
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
POST /api/v1/auth/password-resets
POST /api/v1/auth/password-resets/complete
GET /api/v1/invitations/{token}/summary
POST /api/v1/invitations/{token}/accept
GET /api/v1/me
~~~

Liveness:

~~~python
@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}
~~~

Readiness:

~~~python
@app.get("/health/ready")
async def ready() -> dict[str, str]:
    try:
        await database_is_ready()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="database unavailable",
        ) from error
    return {"status": "ready"}
~~~

The readiness function performs a real SELECT 1 through Psycopg. It therefore
tests more than whether the Python process exists.

Run the API directly on the host:

~~~powershell
$env:NFM_DATABASE_URL = "postgresql://forest_monitor:forest_monitor@localhost:5433/forest_monitor"
.venv\Scripts\python.exe -m uvicorn apps.api.app.main:app --reload --port 8000
~~~

Open API documentation:

~~~text
http://localhost:8000/docs
~~~

Current boundary: the session, invitation-acceptance, password-reset, and
current-profile endpoints are implemented. Organisation administration, site,
schedule, job, observation, event, and review routes remain later Phase 4
batches.

## 15. Authentication and authorisation

### Password hashing

Passwords are validated and hashed with Argon2id. A server-side pepper is
applied before hashing.

The database stores:

- The Argon2id hash.
- Hash version.
- Password-change timestamp.
- Failed login count.
- Temporary lock time.
- Credential status.

It never stores plaintext or reversible passwords.

### Access tokens

Access tokens are short-lived JWTs containing stable claims only:

~~~json
{
  "sub": "user UUID",
  "org": "organisation UUID",
  "sid": "session UUID",
  "iss": "nigeria-forest-monitor",
  "aud": "nfm-api"
}
~~~

Role and email are intentionally absent. Authorisation is resolved from current
database state so a stale token cannot preserve an old role.

### Refresh tokens

Refresh tokens are:

- Cryptographically random opaque strings.
- Stored only as keyed hashes.
- Rotated every time they are used.
- Grouped into a token family.

If an already-rotated token is reused, the service assumes possible token theft,
revokes the entire family, and records a security audit event.

### Invitations and password resets

Invitation and reset secrets are:

- Random.
- Hashed before storage.
- Single-use.
- Expiring.
- Rejected on replay.

Accepting an invitation permanently binds the user to one organisation and one
department.

### Roles

Implemented roles:

- owner
- administrator
- analyst
- verification officer
- viewer

Examples:

- Owners can perform every defined action.
- Administrators manage users, sites, and monitoring.
- Analysts perform remote reviews.
- Verification officers require an explicit assigned referral.
- Viewers see approved summaries only.
- Sensitive sites require administrator/owner access or an analyst team grant.

The last active owner cannot be disabled or demoted.

### Audit events

Privileged and security actions write an audit event in the same transaction as
the state change.

Database trigger:

~~~sql
CREATE TRIGGER audit_events_immutable
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
~~~

An application bug therefore cannot silently rewrite audit history.

## 16. Airflow orchestration

Airflow is used for technical batch orchestration, not as the product database.

Current Dockerfile:

~~~dockerfile
FROM apache/airflow:3.3.0-python3.11

COPY --chown=airflow:root pyproject.toml README.md /opt/nfm/
COPY --chown=airflow:root packages/forest_monitor /opt/nfm/packages/forest_monitor

USER airflow
RUN pip install --no-cache-dir --no-deps -e /opt/nfm

COPY --chown=airflow:root apps/orchestrator/dags /opt/airflow/dags
~~~

Important details:

- The image runs as the airflow user, not root.
- The shared analysis package is installed into the Airflow image.
- DAGs are also bind-mounted locally for rapid development.
- Airflow uses its own database and credentials.
- LocalExecutor is adequate for this local phase.

Current smoke DAG verifies that Airflow can import the shared package and load
configuration. It has no schedule and must be triggered manually.

Useful commands:

~~~powershell
docker compose ps airflow
docker compose logs --tail=200 airflow
docker compose logs -f airflow
docker compose exec airflow airflow dags list
docker compose exec airflow airflow dags list-import-errors
docker compose exec airflow airflow dags test nfm_system_smoke 2026-01-01
~~~

Trigger through the CLI:

~~~powershell
docker compose exec airflow airflow dags trigger nfm_system_smoke
~~~

Airflow UI:

~~~text
http://localhost:8080
~~~

Future processing flow:

1. FastAPI creates an idempotent product job.
2. FastAPI triggers a parameterised Airflow DAG.
3. Airflow loads authorised job inputs.
4. Tasks call the shared analysis package.
5. Airflow sends authenticated, idempotent callbacks.
6. FastAPI commits product-visible results.

## 17. Restricted TiTiler service

TiTiler turns COG rasters into web tiles and provides raster metadata endpoints.
Our service limits it to the configured raster root.

Security check:

~~~python
candidate = Path(url)
if not candidate.is_absolute():
    candidate = RASTER_ROOT / candidate
candidate = candidate.resolve()

if not candidate.is_relative_to(RASTER_ROOT):
    raise HTTPException(
        status_code=403,
        detail="raster path is outside the configured storage root",
    )
~~~

This prevents path traversal such as requesting ../../secret-file.

The Docker mount is read-only:

~~~yaml
volumes:
  - ./data/rasters:/data/rasters:ro
~~~

Useful commands:

~~~powershell
docker compose build tiles
docker compose up -d tiles
docker compose logs --tail=100 tiles
Invoke-RestMethod http://localhost:8001/health/live
~~~

Current limitation: real, provenance-verified TIFF/COG ingestion and catalogue
publication belong to later phases.

## 18. Testing and quality checks

### Compile Python

~~~powershell
.venv\Scripts\python.exe -m compileall -q apps\api apps\tiles apps\orchestrator packages\forest_monitor\src
~~~

### Lint

~~~powershell
.venv\Scripts\ruff.exe check apps packages\forest_monitor
~~~

Apply safe Ruff fixes:

~~~powershell
.venv\Scripts\ruff.exe check apps packages\forest_monitor --fix
~~~

### Format

~~~powershell
.venv\Scripts\ruff.exe format apps packages\forest_monitor
.venv\Scripts\ruff.exe format --check apps packages\forest_monitor
~~~

### Run backend tests without PostgreSQL integration

~~~powershell
.venv\Scripts\python.exe -m pytest -q -m "not integration"
~~~

### Run live PostgreSQL/PostGIS tests

~~~powershell
docker compose up -d postgres
docker compose up --force-recreate postgres-init
$env:NFM_RUN_DB_TESTS = "1"
.venv\Scripts\python.exe -m pytest apps\api\tests\integration -q
~~~

The integration tests verify:

- Migration upgrade.
- Migration rollback.
- PostGIS availability.
- Cross-tenant reads are hidden.
- Cross-tenant writes are rejected.
- Viewport/site spatial queries return correct cells.
- Audit events cannot be modified or deleted.
- Invitation replay is rejected.
- Refresh-token reuse revokes its family.
- Password-reset replay is rejected.

### Validate Compose

~~~powershell
docker compose config --quiet
~~~

### Backend-focused validation sequence

~~~powershell
.venv\Scripts\python.exe -m compileall -q apps\api apps\tiles apps\orchestrator packages\forest_monitor\src
.venv\Scripts\ruff.exe check apps packages\forest_monitor
.venv\Scripts\ruff.exe format --check apps packages\forest_monitor
.venv\Scripts\python.exe -m pytest -q -m "not integration"
$env:NFM_RUN_DB_TESTS = "1"
.venv\Scripts\python.exe -m pytest apps\api\tests\integration -q
docker compose config --quiet
~~~

## 19. Database backup and restore

Create a custom-format product database backup:

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-database.ps1
~~~

The script:

1. Validates that the target remains inside the repository.
2. Runs pg_dump inside the PostgreSQL container.
3. Copies the dump to the ignored backups directory.
4. Removes the temporary container file.

Restore:

~~~powershell
powershell -ExecutionPolicy Bypass -File scripts\restore-database.ps1 -BackupPath backups\forest-monitor-TIMESTAMP.dump
~~~

Restore requires the exact confirmation phrase because it overwrites the local
product database. It does not restore raster bytes or the Airflow database.

Manual database inspection:

~~~powershell
docker compose exec -T postgres psql -U postgres -d forest_monitor
~~~

Useful SQL:

~~~sql
SELECT version();
SELECT PostGIS_Version();
SELECT version_num FROM alembic_version;
SELECT count(*) FROM sites;
~~~

When querying an RLS-protected table as the application role, set the tenant
context inside the transaction:

~~~sql
SELECT set_config(
  'app.current_organisation_id',
  '10000000-0000-4000-8000-000000000001',
  true
);
SELECT id, name FROM sites;
~~~

## 20. Configuration and secrets

The committed .env.example documents required variables. The real .env file is
ignored by Git.

Important variables:

~~~text
NFM_POSTGRES_PORT
NFM_DATABASE_URL
NFM_ACCESS_TOKEN_SECRET
NFM_PASSWORD_PEPPER
NFM_SEED_ADMIN_EMAIL
NFM_SEED_ADMIN_PASSWORD
NFM_RASTER_ROOT
~~~

Compose syntax supports defaults. Conceptually:

~~~text
Use NFM_API_PORT when set; otherwise use 8000.
~~~

Local defaults are not production secrets. Before any hosted deployment:

- Generate strong independent secrets.
- Store secrets in the hosting platform's secret manager.
- Rotate seed credentials.
- Disable or redesign automatic production seeding.
- Restrict database network access.
- Use TLS.
- Do not expose Airflow to product users.

## 21. Git workflow used

We preserved the user's existing staged boundary and pushed two ordered commits:

~~~text
af7c501 refactor: move analysis package into monorepo layout
355876a feat: establish local monitoring platform foundation
~~~

Commands:

~~~powershell
git status --short
git diff --cached --name-status
git commit -m "refactor: move analysis package into monorepo layout"
git push origin main

git add -A
git diff --cached --check
git commit -m "feat: establish local monitoring platform foundation"
git push origin main
~~~

Before the second commit we:

- Enumerated all untracked files.
- Checked ignored files.
- Scanned for common credential patterns.
- Checked for unexpectedly large files.
- Expanded .gitignore.
- Ran git diff --cached --check.

Useful Git inspection commands:

~~~powershell
git status -sb
git diff
git diff --cached
git log --oneline --decorate -10
git remote -v
~~~

## 22. A strong interview response to a failed Docker log

Suppose the interviewer gives:

~~~text
api-1 | psycopg.OperationalError:
api-1 | connection to server at "postgres", port 5432 failed:
api-1 | Connection refused
~~~

A strong verbal answer:

> The API is resolving the expected Compose hostname but PostgreSQL is not
> accepting connections. I would first check service and health state with
> docker compose ps, then inspect PostgreSQL logs with docker compose logs
> --tail=100 postgres. I would validate the resolved Compose configuration with
> docker compose config. If PostgreSQL is healthy but the API started too early,
> I would check depends_on health conditions. I would not delete volumes before
> understanding the failure.

Commands:

~~~powershell
docker compose ps
docker compose logs --tail=100 postgres
docker compose logs --tail=100 api
docker compose config
docker compose exec -T postgres pg_isready -U postgres -d postgres
~~~

If PostgreSQL is stopped:

~~~powershell
docker compose up -d postgres postgres-init
~~~

If API configuration changed:

~~~powershell
docker compose up -d --force-recreate api
~~~

If the API image changed:

~~~powershell
docker compose up -d --build api
~~~

The key distinction:

~~~text
restart        -> same container configuration and image
force-recreate -> new container from current configuration
build          -> create/update image
up             -> converge services to declared state
~~~

## 23. Practice scenarios

### Scenario 1: API container exits immediately

~~~powershell
docker compose ps -a
docker compose logs --tail=200 api
~~~

Look for migration, import, configuration, or command errors.

### Scenario 2: API is running but unhealthy

~~~powershell
docker compose ps api
docker inspect nigeria-forest-monitor-api-1
docker compose logs --tail=100 api
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health/live').read())"
~~~

### Scenario 3: PostgreSQL authentication failed

~~~powershell
docker compose config
docker compose logs --tail=100 postgres
docker compose exec -T postgres psql -U postgres -d postgres -Atc "SELECT rolname FROM pg_roles"
~~~

Compare configured usernames, passwords, database names, and the actual target
server.

### Scenario 4: Airflow cannot import the DAG

~~~powershell
docker compose logs --tail=200 airflow
docker compose exec airflow airflow dags list-import-errors
docker compose exec airflow python -c "import forest_monitor; print(forest_monitor.__file__)"
~~~

### Scenario 5: Tile request says raster not found

~~~powershell
docker compose exec tiles sh -c "find /data/rasters -maxdepth 2 -type f"
docker compose logs --tail=100 tiles
~~~

Check the bind mount, requested path, file existence, and raster-root
restriction.

### Scenario 6: Port already allocated

~~~powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
docker compose ps
docker compose config
~~~

Either stop the conflicting process or change only the host port mapping.

## 24. Current verified state

At the end of Phase 3:

- Alembic revision 0001_phase3 is applied.
- The API container builds and becomes healthy.
- The API readiness endpoint can query PostgreSQL.
- PostgreSQL and Airflow metadata are isolated.
- Tenant RLS and spatial queries pass live integration tests.
- Authentication security flows pass replay tests.
- Migration upgrade and rollback pass in a disposable database.
- The backend Python checks pass.
- Compose configuration validates.

Current intentional gaps:

- Full domain API endpoints are Phase 4.
- The Airflow DAG is currently a system smoke test, not the final monitoring
  pipeline.
- Verified real protected-area boundary imports are pending.
- Real catalogue discovery and COG processing are pending.
- Hosted secrets, deployment, observability, and production recovery objectives
  belong to later phases.

## 25. Daily backend command card

Start:

~~~powershell
docker compose up -d postgres postgres-init api tiles airflow
~~~

State:

~~~powershell
docker compose ps
~~~

Logs:

~~~powershell
docker compose logs --tail=100 SERVICE
docker compose logs -f SERVICE
~~~

Rebuild one service:

~~~powershell
docker compose up -d --build SERVICE
~~~

Database revision:

~~~powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini current
~~~

Backend tests:

~~~powershell
.venv\Scripts\python.exe -m pytest -q -m "not integration"
$env:NFM_RUN_DB_TESTS = "1"
.venv\Scripts\python.exe -m pytest apps\api\tests\integration -q
~~~

Stop safely:

~~~powershell
docker compose down
~~~

Avoid unless intentionally deleting local database state:

~~~powershell
docker compose down -v
~~~

The best habit is: inspect state, read the relevant log, form one hypothesis,
run the smallest diagnostic command, make the smallest targeted change, and
verify with health plus tests.
