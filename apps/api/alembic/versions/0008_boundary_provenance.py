"""Add boundary authorship, change reasons, and immutability guards.

Revision ID: 0008_boundary_provenance
Revises: 0007_site_resources
"""

from alembic import op

revision = "0008_boundary_provenance"
down_revision = "0007_site_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE site_boundary_versions ADD COLUMN created_by uuid")
    op.execute("ALTER TABLE site_boundary_versions ADD COLUMN change_reason text")
    op.execute("ALTER TABLE sites DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_boundary_versions DISABLE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE site_boundary_versions b
        SET created_by=s.created_by,change_reason='Initial boundary'
        FROM sites s
        WHERE s.organisation_id=b.organisation_id AND s.id=b.site_id
        """
    )
    op.execute("ALTER TABLE sites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sites FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_boundary_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_boundary_versions FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_boundary_versions ALTER COLUMN created_by SET NOT NULL")
    op.execute("ALTER TABLE site_boundary_versions ALTER COLUMN change_reason SET NOT NULL")
    op.execute(
        """
        ALTER TABLE site_boundary_versions
        ADD CONSTRAINT boundary_created_by_fk
        FOREIGN KEY (organisation_id,created_by)
        REFERENCES user_profiles(organisation_id,id),
        ADD CONSTRAINT boundary_change_reason_length
        CHECK (char_length(change_reason) BETWEEN 3 AND 500)
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_boundary_content_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'boundary versions are immutable' USING ERRCODE='55000';
          END IF;
          IF OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL
             AND NEW.id=OLD.id
             AND NEW.organisation_id=OLD.organisation_id
             AND NEW.site_id=OLD.site_id
             AND NEW.version=OLD.version
             AND ST_Equals(NEW.geometry,OLD.geometry)
             AND NEW.source_authority=OLD.source_authority
             AND NEW.source_identifier=OLD.source_identifier
             AND NEW.source_url IS NOT DISTINCT FROM OLD.source_url
             AND NEW.licence=OLD.licence
             AND NEW.attribution=OLD.attribution
             AND NEW.effective_date IS NOT DISTINCT FROM OLD.effective_date
             AND NEW.source_crs=OLD.source_crs
             AND NEW.validation_result=OLD.validation_result
             AND NEW.checksum=OLD.checksum
             AND NEW.created_by=OLD.created_by
             AND NEW.change_reason=OLD.change_reason
             AND NEW.created_at=OLD.created_at THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'boundary versions are immutable' USING ERRCODE='55000';
        END;
        $$;
        CREATE TRIGGER boundary_content_immutable
        BEFORE UPDATE OR DELETE ON site_boundary_versions
        FOR EACH ROW EXECUTE FUNCTION reject_boundary_content_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER boundary_content_immutable ON site_boundary_versions")
    op.execute("DROP FUNCTION reject_boundary_content_mutation")
    op.execute("ALTER TABLE site_boundary_versions DROP CONSTRAINT boundary_change_reason_length")
    op.execute("ALTER TABLE site_boundary_versions DROP CONSTRAINT boundary_created_by_fk")
    op.execute("ALTER TABLE site_boundary_versions DROP COLUMN change_reason")
    op.execute("ALTER TABLE site_boundary_versions DROP COLUMN created_by")
