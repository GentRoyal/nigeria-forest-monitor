# API

FastAPI is the sole operational boundary for the browser and Airflow. Phase 3
adds the PostgreSQL/PostGIS schema, forced organisation RLS, local identity
services, and audited role enforcement. Domain HTTP endpoints begin in Phase 4.

Run migrations and the idempotent local seed from the repository root:

```powershell
.venv\Scripts\python.exe -m alembic -c apps\api\alembic.ini upgrade head
.venv\Scripts\python.exe -m apps.api.app.db.seed
```

The seeded local owner is configured with NFM_SEED_ADMIN_EMAIL and
NFM_SEED_ADMIN_PASSWORD. Defaults are development-only. The API container
applies migrations and the seed before starting.

To run the database acceptance tests, start PostgreSQL and set the opt-in flag:

```powershell
docker compose up -d postgres postgres-init
$env:NFM_RUN_DB_TESTS = "1"
.venv\Scripts\python.exe -m pytest apps\api\tests\integration -q
```

```powershell
$env:PYTHONPATH = "apps/api"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```
