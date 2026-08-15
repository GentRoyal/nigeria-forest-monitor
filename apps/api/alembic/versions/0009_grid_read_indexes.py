"""Add read-path indexes for grid history and viewport cell queries.

Revision ID: 0009_grid_read_indexes
Revises: 0008_boundary_provenance
"""

from alembic import op

revision = "0009_grid_read_indexes"
down_revision = "0008_boundary_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX grid_versions_site_history_idx "
        "ON grid_versions(organisation_id,site_id,created_at DESC,id DESC)"
    )
    op.execute(
        "CREATE INDEX grid_cells_version_history_idx "
        "ON grid_cells(organisation_id,grid_version_id,created_at DESC,id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX grid_cells_version_history_idx")
    op.execute("DROP INDEX grid_versions_site_history_idx")
