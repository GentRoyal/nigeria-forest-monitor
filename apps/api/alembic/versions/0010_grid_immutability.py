"""Protect immutable grid-version content.

Revision ID: 0010_grid_immutability
Revises: 0009_grid_read_indexes
"""

from alembic import op

revision = "0010_grid_immutability"
down_revision = "0009_grid_read_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_grid_content_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'grid versions are immutable' USING ERRCODE='55000';
          END IF;
          IF OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL
             AND NEW.id=OLD.id AND NEW.organisation_id=OLD.organisation_id
             AND NEW.site_id=OLD.site_id AND NEW.version=OLD.version
             AND NEW.method=OLD.method AND NEW.resolution_metres=OLD.resolution_metres
             AND NEW.parameters=OLD.parameters AND NEW.creation_reason=OLD.creation_reason
             AND NEW.processing_compatibility=OLD.processing_compatibility
             AND NEW.created_at=OLD.created_at THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'grid versions are immutable' USING ERRCODE='55000';
        END;
        $$;
        CREATE TRIGGER grid_content_immutable
        BEFORE UPDATE OR DELETE ON grid_versions
        FOR EACH ROW EXECUTE FUNCTION reject_grid_content_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER grid_content_immutable ON grid_versions")
    op.execute("DROP FUNCTION reject_grid_content_mutation")
