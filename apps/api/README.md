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

The implemented Phase 4 identity endpoints are:

- POST /api/v1/auth/login
- POST /api/v1/auth/refresh
- POST /api/v1/auth/logout
- POST /api/v1/auth/password-resets
- POST /api/v1/auth/password-resets/complete
- GET /api/v1/invitations/{token}/summary
- POST /api/v1/invitations/{token}/accept
- GET /api/v1/me

Access tokens use the Bearer scheme. Refresh tokens rotate in an HttpOnly
SameSite cookie; cookie-based refresh requires the matching X-CSRF-Token header.
Only the local environment accepts the refresh token in the request body for
OpenAPI/manual testing.

Password-reset request responses are generic. Only the local environment returns
a development reset token while email delivery is not yet connected.

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
