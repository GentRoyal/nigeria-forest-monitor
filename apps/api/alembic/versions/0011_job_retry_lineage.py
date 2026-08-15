"""Link retry jobs to their original job.

Revision ID: 0011_job_retry_lineage
Revises: 0010_grid_immutability
"""

from alembic import op

revision = "0011_job_retry_lineage"
down_revision = "0010_grid_immutability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE processing_jobs ADD COLUMN retry_of_job_id uuid")
    op.execute(
        """
        ALTER TABLE processing_jobs ADD CONSTRAINT processing_jobs_retry_of_fk
        FOREIGN KEY (organisation_id,retry_of_job_id)
        REFERENCES processing_jobs(organisation_id,id)
        """
    )
    op.execute(
        "CREATE INDEX processing_jobs_retry_lineage_idx "
        "ON processing_jobs(organisation_id,retry_of_job_id,created_at DESC) "
        "WHERE retry_of_job_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX processing_jobs_retry_lineage_idx")
    op.execute("ALTER TABLE processing_jobs DROP CONSTRAINT processing_jobs_retry_of_fk")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN retry_of_job_id")
