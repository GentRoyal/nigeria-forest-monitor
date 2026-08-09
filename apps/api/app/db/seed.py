import asyncio
from uuid import UUID

from ..security.passwords import hash_password
from ..settings import get_settings
from .connection import system_connection, tenant_connection

ORGANISATION_ID = UUID("10000000-0000-4000-8000-000000000001")
DEPARTMENT_ID = UUID("20000000-0000-4000-8000-000000000001")
OWNER_ID = UUID("30000000-0000-4000-8000-000000000001")
TEAM_ID = UUID("40000000-0000-4000-8000-000000000001")

SITE_ROWS = (
    (UUID("50000000-0000-4000-8000-000000000001"), "Old Oyo National Park", "old-oyo"),
    (UUID("50000000-0000-4000-8000-000000000002"), "Kainji Lake National Park", "kainji"),
    (
        UUID("50000000-0000-4000-8000-000000000003"),
        "Old Oyo–Kwara–Kainji Monitoring Corridor",
        "old-oyo-kwara-kainji",
    ),
)


async def seed() -> None:
    settings = get_settings()
    async with system_connection() as connection:
        await connection.execute(
            """
            INSERT INTO workspace_templates(version,name,defaults)
            VALUES (1,'Government forestry workspace v1',%s::jsonb)
            ON CONFLICT (version) DO NOTHING
            """,
            ('{"roles":["owner","administrator","analyst","verification_officer","viewer"]}',),
        )

    async with tenant_connection(ORGANISATION_ID) as connection:
        await connection.execute(
            """
            INSERT INTO organisations(id,name,slug,workspace_template_version)
            VALUES (%s,'Nigeria Forest Monitor Local Pilot','nfm-local-pilot',1)
            ON CONFLICT (id) DO NOTHING
            """,
            (ORGANISATION_ID,),
        )
        await connection.execute(
            """
            INSERT INTO departments(id,organisation_id,name)
            VALUES (%s,%s,'Forest Monitoring')
            ON CONFLICT (id) DO NOTHING
            """,
            (DEPARTMENT_ID, ORGANISATION_ID),
        )
        await connection.execute(
            """
            INSERT INTO user_profiles(
              id,organisation_id,primary_department_id,email,display_name,
              role,status,activated_at
            ) VALUES (%s,%s,%s,%s,'Local Workspace Owner','owner','active',now())
            ON CONFLICT (id) DO NOTHING
            """,
            (
                OWNER_ID,
                ORGANISATION_ID,
                DEPARTMENT_ID,
                settings.seed_admin_email.lower(),
            ),
        )
        await connection.execute(
            """
            INSERT INTO auth_credentials(user_id,organisation_id,password_hash)
            VALUES (%s,%s,%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (OWNER_ID, ORGANISATION_ID, hash_password(settings.seed_admin_password)),
        )
        await connection.execute(
            """
            INSERT INTO teams(id,organisation_id,department_id,name)
            VALUES (%s,%s,%s,'Remote Analysis')
            ON CONFLICT (id) DO NOTHING
            """,
            (TEAM_ID, ORGANISATION_ID, DEPARTMENT_ID),
        )
        await connection.execute(
            """
            INSERT INTO team_memberships(organisation_id,team_id,user_id,created_by)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (organisation_id,team_id,user_id) DO NOTHING
            """,
            (ORGANISATION_ID, TEAM_ID, OWNER_ID, OWNER_ID),
        )
        for site_id, name, slug in SITE_ROWS:
            await connection.execute(
                """
                INSERT INTO sites(
                  id,organisation_id,managing_department_id,name,slug,origin,created_by
                ) VALUES (%s,%s,%s,%s,%s,'predefined',%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (site_id, ORGANISATION_ID, DEPARTMENT_ID, name, slug, OWNER_ID),
            )


if __name__ == "__main__":
    asyncio.run(seed())
