import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege, RaiseException

from apps.api.app.security.auth import AuthError, AuthService
from apps.api.app.security.passwords import hash_password
from apps.api.app.security.permissions import Role

DATABASE_URL = os.getenv(
    "NFM_DATABASE_URL",
    "postgresql://forest_monitor:forest_monitor@localhost:5433/forest_monitor",
)
POSTGRES_ADMIN_URL = os.getenv(
    "NFM_TEST_POSTGRES_ADMIN_URL",
    "postgresql://postgres:postgres-local-only@localhost:5433/postgres",
)

pytestmark = pytest.mark.integration
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def connect():
    return psycopg.connect(DATABASE_URL)


def set_tenant(connection, organisation_id: UUID, user_id: UUID | None = None) -> None:
    connection.execute(
        "SELECT set_config('app.current_organisation_id', %s, false)",
        (str(organisation_id),),
    )
    connection.execute(
        "SELECT set_config('app.current_user_id', %s, false)",
        (str(user_id) if user_id else "",),
    )


def provision_workspace(name: str) -> tuple[UUID, UUID, UUID]:
    organisation_id, department_id, owner_id = uuid4(), uuid4(), uuid4()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO workspace_templates(version,name)
            VALUES (9999,'Integration test workspace')
            ON CONFLICT (version) DO NOTHING
            """
        )
        set_tenant(connection, organisation_id)
        connection.execute(
            """
            INSERT INTO organisations(id,name,slug,workspace_template_version)
            VALUES (%s,%s,%s,9999)
            """,
            (organisation_id, name, f"test-{organisation_id}"),
        )
        connection.execute(
            "INSERT INTO departments(id,organisation_id,name) VALUES (%s,%s,'Monitoring')",
            (department_id, organisation_id),
        )
        connection.execute(
            """
            INSERT INTO user_profiles(
              id,organisation_id,primary_department_id,email,display_name,role,status,activated_at
            ) VALUES (%s,%s,%s,%s,'Test Owner','owner','active',now())
            """,
            (owner_id, organisation_id, department_id, f"{owner_id}@test.local"),
        )
        connection.execute(
            """
            INSERT INTO auth_credentials(user_id,organisation_id,password_hash)
            VALUES (%s,%s,%s)
            """,
            (owner_id, organisation_id, hash_password("Forest-Owner-2026!")),
        )
    return organisation_id, department_id, owner_id


def test_migration_upgrade_and_rollback_round_trip() -> None:
    if os.getenv("NFM_RUN_DB_TESTS") != "1":
        pytest.skip("set NFM_RUN_DB_TESTS=1 to run PostgreSQL integration tests")
    database_name = f"migration_test_{uuid4().hex}"
    parsed_url = urlsplit(DATABASE_URL)
    isolated_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, f"/{database_name}", "", ""))
    try:
        with psycopg.connect(POSTGRES_ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'CREATE DATABASE "{database_name}" OWNER forest_monitor')
        admin_database_url = urlunsplit(
            (
                urlsplit(POSTGRES_ADMIN_URL).scheme,
                urlsplit(POSTGRES_ADMIN_URL).netloc,
                f"/{database_name}",
                "",
                "",
            )
        )
        with psycopg.connect(admin_database_url, autocommit=True) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        environment = {**os.environ, "NFM_DATABASE_URL": isolated_url}
        upgrade = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "upgrade",
                "head",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert upgrade.returncode == 0, upgrade.stderr
        with psycopg.connect(isolated_url) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name='sites'"
                ).fetchone()[0]
                == 1
            )
        downgrade = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "apps/api/alembic.ini",
                "downgrade",
                "base",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert downgrade.returncode == 0, downgrade.stderr
        with psycopg.connect(isolated_url) as connection:
            assert (
                connection.execute(
                    """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema='public'
                  AND table_name IN ('organisations','sites','audit_events')
                """
                ).fetchone()[0]
                == 0
            )
    finally:
        with psycopg.connect(POSTGRES_ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')


@pytest.fixture(scope="module")
def workspaces() -> dict[str, tuple[UUID, UUID, UUID]]:
    if os.getenv("NFM_RUN_DB_TESTS") != "1":
        pytest.skip("set NFM_RUN_DB_TESTS=1 to run PostgreSQL integration tests")
    try:
        with connect() as connection:
            connection.execute("SELECT PostGIS_Version()").fetchone()
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if not revision or revision[0] != "0002_auth_api":
                pytest.skip("Phase 3 migration is not applied")
    except psycopg.OperationalError:
        pytest.skip("local PostgreSQL is unavailable")
    return {
        "a": provision_workspace("Tenant A"),
        "b": provision_workspace("Tenant B"),
    }


def test_rls_hides_other_organisation_rows(workspaces) -> None:
    org_a, _, _ = workspaces["a"]
    org_b, _, _ = workspaces["b"]
    with connect() as connection:
        set_tenant(connection, org_a)
        visible = connection.execute("SELECT id FROM organisations ORDER BY id").fetchall()
        assert visible == [(org_a,)]
        assert (
            connection.execute(
                "SELECT count(*) FROM departments WHERE organisation_id=%s", (org_b,)
            ).fetchone()[0]
            == 0
        )


def test_rls_rejects_cross_tenant_writes(workspaces) -> None:
    org_a, _, owner_a = workspaces["a"]
    org_b, department_b, _ = workspaces["b"]
    with connect() as connection:
        set_tenant(connection, org_a, owner_a)
        with pytest.raises(InsufficientPrivilege):
            connection.execute(
                """
                INSERT INTO sites(
                  organisation_id,managing_department_id,name,slug,origin,created_by
                ) VALUES (%s,%s,'Cross tenant','cross-tenant','custom',%s)
                """,
                (org_b, department_b, owner_a),
            )


def test_spatial_queries_return_correct_grid_cells(workspaces) -> None:
    organisation_id, department_id, owner_id = workspaces["a"]
    site_id, boundary_id, grid_id = uuid4(), uuid4(), uuid4()
    with connect() as connection:
        set_tenant(connection, organisation_id, owner_id)
        connection.execute(
            """
            INSERT INTO sites(
              id,organisation_id,managing_department_id,name,slug,origin,created_by
            ) VALUES (%s,%s,%s,'Synthetic spatial fixture',%s,'custom',%s)
            """,
            (site_id, organisation_id, department_id, f"spatial-{site_id}", owner_id),
        )
        connection.execute(
            """
            INSERT INTO site_boundary_versions(
              id,organisation_id,site_id,version,geometry,source_authority,
              source_identifier,licence,attribution,source_crs,checksum
            ) VALUES (
              %s,%s,%s,1,
              ST_Multi(ST_GeomFromText('POLYGON((3 8,5 8,5 10,3 10,3 8))',4326)),
              'Test fixture','synthetic','CC0','Integration test','EPSG:4326','fixture'
            )
            """,
            (boundary_id, organisation_id, site_id),
        )
        connection.execute(
            """
            INSERT INTO grid_versions(
              id,organisation_id,site_id,version,method,resolution_metres,
              creation_reason,processing_compatibility
            ) VALUES (%s,%s,%s,1,'synthetic',100000,'test','v1')
            """,
            (grid_id, organisation_id, site_id),
        )
        for key, wkt in (
            ("inside-west", "POLYGON((3 8,4 8,4 9,3 9,3 8))"),
            ("inside-east", "POLYGON((4 8,5 8,5 9,4 9,4 8))"),
            ("outside", "POLYGON((6 8,7 8,7 9,6 9,6 8))"),
        ):
            connection.execute(
                """
                INSERT INTO grid_cells(
                  organisation_id,grid_version_id,cell_key,geometry,area_sq_m
                ) VALUES (%s,%s,%s,ST_GeomFromText(%s,4326),1000000)
                """,
                (organisation_id, grid_id, key, wkt),
            )
        site_cells = connection.execute(
            """
            SELECT c.cell_key FROM grid_cells c
            JOIN site_boundary_versions b ON b.organisation_id=c.organisation_id
            WHERE b.id=%s AND c.grid_version_id=%s AND ST_Intersects(c.geometry,b.geometry)
            ORDER BY c.cell_key
            """,
            (boundary_id, grid_id),
        ).fetchall()
        viewport_cells = connection.execute(
            """
            SELECT cell_key FROM grid_cells
            WHERE grid_version_id=%s
              AND ST_Intersects(geometry,ST_MakeEnvelope(3.1,8.1,3.9,8.9,4326))
            """,
            (grid_id,),
        ).fetchall()
    assert site_cells == [("inside-east",), ("inside-west",)]
    assert viewport_cells == [("inside-west",)]


def test_audit_events_are_immutable(workspaces) -> None:
    organisation_id, _, owner_id = workspaces["a"]
    with connect() as connection:
        set_tenant(connection, organisation_id, owner_id)
        audit_id = connection.execute(
            """
            INSERT INTO audit_events(organisation_id,actor_id,action,target_type)
            VALUES (%s,%s,'test.created','test') RETURNING id
            """,
            (organisation_id, owner_id),
        ).fetchone()[0]
    with connect() as connection:
        set_tenant(connection, organisation_id, owner_id)
        with pytest.raises(RaiseException):
            connection.execute("DELETE FROM audit_events WHERE id=%s", (audit_id,))


def test_invitation_rotation_reuse_and_reset_replay(workspaces) -> None:
    organisation_id, department_id, owner_id = workspaces["b"]
    service = AuthService()

    async def scenario() -> None:
        invitation = await service.create_invitation(
            organisation_id=organisation_id,
            department_id=department_id,
            email=f"analyst-{uuid4()}@test.local",
            role=Role.ANALYST,
            invited_by=owner_id,
        )
        user_id = await service.accept_invitation(
            organisation_id=organisation_id,
            token=invitation,
            display_name="Test Analyst",
            password="Forest-Analyst-2026!",
        )
        with pytest.raises(AuthError):
            await service.accept_invitation(
                organisation_id=organisation_id,
                token=invitation,
                display_name="Replay",
                password="Forest-Analyst-2026!",
            )
        first = await service.login(
            organisation_id=organisation_id,
            email=(await _email_for(organisation_id, user_id)),
            password="Forest-Analyst-2026!",
        )
        second = await service.refresh(
            organisation_id=organisation_id,
            refresh_token=first.refresh_token,
        )
        with pytest.raises(AuthError):
            await service.refresh(
                organisation_id=organisation_id,
                refresh_token=first.refresh_token,
            )
        with pytest.raises(AuthError):
            await service.refresh(
                organisation_id=organisation_id,
                refresh_token=second.refresh_token,
            )
        reset = await service.request_password_reset(
            organisation_id=organisation_id,
            email=await _email_for(organisation_id, user_id),
        )
        assert reset
        await service.reset_password(
            organisation_id=organisation_id,
            token=reset,
            new_password="Changed-Forest-2026!",
        )
        with pytest.raises(AuthError):
            await service.reset_password(
                organisation_id=organisation_id,
                token=reset,
                new_password="Changed-Again-2026!",
            )

    async def _email_for(organisation_id: UUID, user_id: UUID) -> str:
        from apps.api.app.db import tenant_connection

        async with tenant_connection(organisation_id, user_id) as connection:
            row = await (
                await connection.execute(
                    "SELECT email::text email FROM user_profiles WHERE id=%s", (user_id,)
                )
            ).fetchone()
            return row["email"]

    asyncio.run(scenario())
