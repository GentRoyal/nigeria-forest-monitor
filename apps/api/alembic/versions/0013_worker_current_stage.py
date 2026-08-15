"""Persist the worker's current processing stage.

Revision ID: 0013_worker_current_stage
Revises: 0012_worker_job_leases
"""

from alembic import op

revision = "0013_worker_current_stage"
down_revision = "0012_worker_job_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE processing_jobs ADD COLUMN current_stage text")


def downgrade() -> None:
    op.execute("ALTER TABLE processing_jobs DROP COLUMN current_stage")
