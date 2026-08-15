"""Add worker lease state to processing jobs.

Revision ID: 0012_worker_job_leases
Revises: 0011_job_retry_lineage
"""

from alembic import op

revision = "0012_worker_job_leases"
down_revision = "0011_job_retry_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE processing_jobs ADD COLUMN worker_identity text")
    op.execute("ALTER TABLE processing_jobs ADD COLUMN lease_token_hash text")
    op.execute("ALTER TABLE processing_jobs ADD COLUMN lease_expires_at timestamptz")
    op.execute("ALTER TABLE processing_jobs ADD COLUMN heartbeat_at timestamptz")
    op.execute("ALTER TABLE processing_jobs ADD COLUMN attempt_count integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE processing_jobs ADD CONSTRAINT processing_jobs_attempt_count_check CHECK (attempt_count >= 0)")
    op.execute(
        "CREATE INDEX processing_jobs_lease_recovery_idx "
        "ON processing_jobs(organisation_id,lease_expires_at) "
        "WHERE status IN ('orchestrating','running','publishing')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX processing_jobs_lease_recovery_idx")
    op.execute("ALTER TABLE processing_jobs DROP CONSTRAINT processing_jobs_attempt_count_check")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN attempt_count")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN heartbeat_at")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN lease_expires_at")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN lease_token_hash")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN worker_identity")
