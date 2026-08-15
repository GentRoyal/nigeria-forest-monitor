"""Add site versions, tags, and authorised search indexes.

Revision ID: 0007_site_resources
Revises: 0006_member_resources
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_site_resources"
down_revision: str | None = "0006_member_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "ALTER TABLE sites ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0)"
    )
    op.execute(
        """
        CREATE TABLE tags (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          organisation_id uuid NOT NULL REFERENCES organisations(id),
          name text NOT NULL,
          created_by uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (organisation_id,id),
          FOREIGN KEY (organisation_id,created_by)
            REFERENCES user_profiles(organisation_id,id)
        );
        CREATE UNIQUE INDEX tags_org_name_ci_unique
          ON tags (organisation_id,lower(name));
        CREATE TABLE site_tags (
          organisation_id uuid NOT NULL,
          site_id uuid NOT NULL,
          tag_id uuid NOT NULL,
          attached_by uuid NOT NULL,
          attached_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (organisation_id,site_id,tag_id),
          FOREIGN KEY (organisation_id,site_id) REFERENCES sites(organisation_id,id),
          FOREIGN KEY (organisation_id,tag_id) REFERENCES tags(organisation_id,id),
          FOREIGN KEY (organisation_id,attached_by)
            REFERENCES user_profiles(organisation_id,id)
        );
        """
    )
    for table_name in ("tags", "site_tags"):
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table_name}
            USING (organisation_id=app_current_organisation_id())
            WITH CHECK (organisation_id=app_current_organisation_id())
            """
        )
    op.execute(
        """
        CREATE INDEX sites_authorised_search_trgm_idx ON sites USING gin (
          (name || ' ' || slug || ' ' || COALESCE(description,'')) gin_trgm_ops
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX sites_org_slug_ci_unique ON sites(organisation_id,lower(slug))"
    )
    op.execute(
        """
        CREATE FUNCTION enforce_active_site_department() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          -- Do not mask row-level security failures with a domain constraint.
          IF NEW.organisation_id <> app_current_organisation_id() THEN
            RETURN NEW;
          END IF;
          IF NEW.status <> 'deleted' AND NOT EXISTS (
            SELECT 1 FROM departments d
            WHERE d.organisation_id=NEW.organisation_id
              AND d.id=NEW.managing_department_id AND d.status='active'
          ) THEN
            RAISE EXCEPTION 'active sites require an active managing department'
              USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER sites_active_department_guard
        BEFORE INSERT OR UPDATE OF managing_department_id,status ON sites
        FOR EACH ROW EXECUTE FUNCTION enforce_active_site_department();
        """
    )
    op.execute("CREATE INDEX site_tags_tag_idx ON site_tags(organisation_id,tag_id,site_id)")


def downgrade() -> None:
    op.execute("DROP TRIGGER sites_active_department_guard ON sites")
    op.execute("DROP FUNCTION enforce_active_site_department")
    op.execute("DROP INDEX sites_org_slug_ci_unique")
    op.execute("DROP INDEX site_tags_tag_idx")
    op.execute("DROP INDEX sites_authorised_search_trgm_idx")
    op.execute("DROP TABLE site_tags")
    op.execute("DROP TABLE tags")
    op.execute("ALTER TABLE sites DROP COLUMN version")
