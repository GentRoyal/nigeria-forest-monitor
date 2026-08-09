"""Add concurrency metadata for organisation administration resources.

Revision ID: 0003_organisation_resources
Revises: 0002_auth_api
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_organisation_resources"
down_revision: str | None = "0002_auth_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE organisations ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0)"
    )
    op.execute(
        "ALTER TABLE departments ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE departments ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0)"
    )
    op.execute(
        "CREATE UNIQUE INDEX departments_org_name_ci_unique "
        "ON departments (organisation_id, lower(name))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX departments_org_name_ci_unique")
    op.execute("ALTER TABLE departments DROP COLUMN version")
    op.execute("ALTER TABLE departments DROP COLUMN updated_at")
    op.execute("ALTER TABLE organisations DROP COLUMN version")
