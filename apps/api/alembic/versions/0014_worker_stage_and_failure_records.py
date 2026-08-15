"""Record idempotent worker stages and safe job failures.

Revision ID: 0014_stage_failure
Revises: 0013_worker_current_stage
"""

from alembic import op

revision = "0014_stage_failure"
down_revision = "0013_worker_current_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE processing_jobs ADD COLUMN failure_summary jsonb")
    op.execute(
        """
        CREATE TABLE processing_job_stage_callbacks (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          organisation_id uuid NOT NULL,
          processing_job_id uuid NOT NULL,
          stage text NOT NULL,
          idempotency_key_hash text NOT NULL,
          details jsonb NOT NULL DEFAULT '{}'::jsonb,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (organisation_id,id),
          UNIQUE (organisation_id,processing_job_id,idempotency_key_hash),
          FOREIGN KEY (organisation_id,processing_job_id)
            REFERENCES processing_jobs(organisation_id,id)
        )
        """
    )
    op.execute(
        "CREATE INDEX processing_job_stage_callbacks_timeline_idx "
        "ON processing_job_stage_callbacks(organisation_id,processing_job_id,created_at)"
    )
    op.execute("ALTER TABLE processing_job_stage_callbacks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE processing_job_stage_callbacks FORCE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY tenant_isolation ON processing_job_stage_callbacks
        USING (organisation_id=app_current_organisation_id())
        WITH CHECK (organisation_id=app_current_organisation_id())"""
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON processing_job_stage_callbacks")
    op.execute("DROP INDEX processing_job_stage_callbacks_timeline_idx")
    op.execute("DROP TABLE processing_job_stage_callbacks")
    op.execute("ALTER TABLE processing_jobs DROP COLUMN failure_summary")
