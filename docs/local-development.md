# Local development

## Prerequisites

- Python 3.11
- Node.js 22 or newer
- Docker Desktop with Compose
- PowerShell

Copy `.env.example` to `.env`. Local defaults are deliberately non-production
credentials. Do not reuse them outside a developer machine.

## Bootstrap and start

From the repository root:

```powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

`bootstrap.ps1` creates `.venv` when needed and installs the shared package,
service development dependencies, and frontend workspace. `dev.ps1` builds and
starts PostgreSQL/PostGIS, FastAPI, TiTiler, Airflow, and Next.js.

## Service checks

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
Invoke-RestMethod http://localhost:8001/health/live
docker compose ps
docker compose logs airflow
```

Readiness fails until the API can query PostgreSQL. Airflow and product data use
separate databases on the same local PostGIS server. Raster requests are limited
to files below `data/rasters`; path traversal and arbitrary host-file access are
rejected.

## Stop and resume

```powershell
docker compose down
docker compose up --build
```

The named PostgreSQL and Airflow-log volumes persist. Use `docker compose down
-v` only when intentionally discarding all local database state.

## Verification

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

Earth Engine credentials are not required for the offline regression suite.
Machine-wide `PROJ_DATA` or `GDAL_DATA` values are isolated at runtime so the
active environment's Rasterio native data is used.

The project publishes PostgreSQL/PostGIS on host port `5433` by default while
containers use `postgres:5432`. This intentionally avoids a common collision
with native PostgreSQL installations on port `5432`. Override
`NFM_POSTGRES_PORT` and `NFM_DATABASE_URL` together if another port is needed.

## Database migrations and tests

The API container upgrades the product schema and runs the idempotent local seed
before starting. For host-side database work:

```powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini current
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini upgrade head
.venv\Scripts\python.exe -m apps.api.app.db.seed
```

Database tests are opt-in so the offline suite does not require Docker:

```powershell
$env:NFM_RUN_DB_TESTS = "1"
.venv\Scripts\python.exe -m pytest apps\api\tests\integration -q
```

The migration round-trip test creates and removes a disposable database. It
never downgrades the developer's forest_monitor database.
