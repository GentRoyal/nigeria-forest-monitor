"""Allow a key to resolve itself before tenant context exists.

Revision ID: 0017_api_key_lookup
Revises: 0016_notify_dedupe
"""

from alembic import op

revision = "0017_api_key_lookup"
down_revision = "0016_notify_dedupe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON api_keys")
    op.execute(
        """CREATE POLICY api_key_tenant_or_self ON api_keys
        USING (
          organisation_id=app_current_organisation_id()
          OR secret_hash=current_setting('app.api_key_secret_hash', true)
        )
        WITH CHECK (organisation_id=app_current_organisation_id())"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY api_key_tenant_or_self ON api_keys")
    op.execute(
        """CREATE POLICY tenant_isolation ON api_keys
        USING (organisation_id=app_current_organisation_id())
        WITH CHECK (organisation_id=app_current_organisation_id())"""
    )
