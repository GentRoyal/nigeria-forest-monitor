# Nigeria Forest Monitor

Nigeria Forest Monitor is becoming a local-first production geospatial system
for recurring, human-reviewed monitoring of protected forest sites. The primary
MVP serves a government organisation operating private workspaces, departments,
teams, controlled verification workflows, and an auditable site history.

This is not a notebook dashboard or a system for declaring wrongdoing. Satellite
signals create reviewable observations and possible change events; authorised
people corroborate and classify them.

## Current architecture

```text
Next.js + MapLibre ──> FastAPI ──> PostgreSQL/PostGIS
         │                │                 ▲
         └──> TiTiler     └──> Airflow ─────┘
                │                 │
         local COG rasters   shared analysis package
```

All services run locally during the current phases. Vercel, Render, Supabase,
and cloud object storage are intentionally deferred until deployment needs and
measured usage justify them.

## Repository layout

```text
apps/web/                    Next.js and MapLibre monitoring shell
apps/api/                    versioned FastAPI service and health checks
apps/tiles/                  restricted local COG tile service
apps/orchestrator/           Airflow DAGs and batch-orchestration image
packages/forest_monitor/     reusable geospatial and analytical Python package
configs/                     analytical configuration
infra/postgres/init/         local product/Airflow database bootstrap
notebooks/forest_monitor.ipynb unified research and validation notebook
docs/                        product, data, architecture, and operations docs
scripts/                     bootstrap, run, and verification commands
```

## Start locally

Prerequisites: Python 3.11, Node.js 22+, Docker Desktop, and PowerShell.

```powershell
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

The stack exposes the web app on `http://localhost:3000`, API documentation on
`http://localhost:8000/docs`, TiTiler on `http://localhost:8001/docs`, and
Airflow on `http://localhost:8080`. The project PostgreSQL is available on
port `5433` by default to avoid collisions with machine-wide PostgreSQL installs.

Detailed setup, health checks, and troubleshooting are in
[docs/local-development.md](docs/local-development.md).

## Validate

The consolidated check runs Python compilation, enforced lint/format checks,
the analytical and service tests, the frontend lint/typecheck/production build,
and Docker Compose validation:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\check.ps1
```

## Analytical workflow

The consolidated notebook remains available for research and detector
validation. Select the `.venv` kernel and run all cells, or use the packaged CLI:

```powershell
.venv\Scripts\python.exe -m forest_monitor.pipeline --start 2025-01-01 --end 2025-02-01 --zone old_oyo_core
```

ACLED integration remains only in the legacy research pipeline. It is not part
of the approved operational event score or government verification workflow.

## Product documentation

- [Backend and infrastructure study guide](docs/backend-infrastructure-study-guide.md)
- [Phase 4 API contract design](docs/api-contract-design.md)
- [Production roadmap](ROADMAP.md)
- [MVP product specification](docs/product-spec.md)
- [Conceptual data model and state machines](docs/data-model.md)
- [Data governance and database recovery](docs/data-governance.md)
- [Local-first Airflow orchestration ADR](docs/architecture/0001-local-first-orchestration.md)
- [Local PostgreSQL authentication ADR](docs/architecture/0002-local-authentication.md)

## Responsible use

Sentinel observations can change because of moisture, flooding, agriculture,
fire, acquisition geometry, or other benign causes. Outputs are decision-support
indicators, never proof of illegal or hostile activity. Field validation is
restricted to authorised government workflows; remote corroboration is the
default.
