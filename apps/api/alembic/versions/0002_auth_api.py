"""Add exact-slug organisation lookup for pre-authentication.

Revision ID: 0002_auth_api
Revises: 0001_phase3
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_auth_api"
down_revision: str | None = "0001_phase3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON organisations")
    op.execute(
        """
        CREATE POLICY organisation_select ON organisations
        FOR SELECT USING (
          id=app_current_organisation_id()
          OR slug=NULLIF(current_setting('app.login_organisation_slug',true),'')
        )
        """
    )
    op.execute(
        """
        CREATE POLICY organisation_insert ON organisations
        FOR INSERT WITH CHECK (id=app_current_organisation_id())
        """
    )
    op.execute(
        """
        CREATE POLICY organisation_update ON organisations
        FOR UPDATE
        USING (id=app_current_organisation_id())
        WITH CHECK (id=app_current_organisation_id())
        """
    )
    op.execute(
        """
        CREATE POLICY organisation_delete ON organisations
        FOR DELETE USING (id=app_current_organisation_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY organisation_delete ON organisations")
    op.execute("DROP POLICY organisation_update ON organisations")
    op.execute("DROP POLICY organisation_insert ON organisations")
    op.execute("DROP POLICY organisation_select ON organisations")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON organisations
        USING (id=app_current_organisation_id())
        WITH CHECK (id=app_current_organisation_id())
        """
    )
