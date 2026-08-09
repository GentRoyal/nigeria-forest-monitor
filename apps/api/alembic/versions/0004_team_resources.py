"""Add team concurrency metadata and lifecycle integrity guards.

Revision ID: 0004_team_resources
Revises: 0003_organisation_resources
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_team_resources"
down_revision: str | None = "0003_organisation_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE teams ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")
    op.execute(
        "ALTER TABLE teams ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0)"
    )
    op.execute(
        "CREATE UNIQUE INDEX teams_org_department_name_ci_unique "
        "ON teams (organisation_id, department_id, lower(name))"
    )
    op.execute(
        """
        CREATE FUNCTION prevent_occupied_department_archive() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status='active' AND NEW.status='archived' AND (
            EXISTS (SELECT 1 FROM user_profiles WHERE primary_department_id=NEW.id)
            OR EXISTS (SELECT 1 FROM teams WHERE department_id=NEW.id AND status='active')
            OR EXISTS (
              SELECT 1 FROM sites
              WHERE managing_department_id=NEW.id AND status<>'deleted'
            )
          ) THEN
            RAISE EXCEPTION 'occupied departments cannot be archived';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER occupied_department_archive_guard
        BEFORE UPDATE OF status ON departments
        FOR EACH ROW EXECUTE FUNCTION prevent_occupied_department_archive();
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_active_team_department() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status='active' AND NOT EXISTS (
            SELECT 1 FROM departments d
            WHERE d.organisation_id=NEW.organisation_id
              AND d.id=NEW.department_id AND d.status='active'
          ) THEN
            RAISE EXCEPTION 'active teams require an active department';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER active_team_department_guard
        BEFORE INSERT OR UPDATE ON teams
        FOR EACH ROW EXECUTE FUNCTION enforce_active_team_department();
        """
    )
    op.execute(
        """
        CREATE FUNCTION deactivate_archived_team_memberships() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status='active' AND NEW.status='archived' THEN
            UPDATE team_memberships SET status='inactive'
            WHERE organisation_id=NEW.organisation_id AND team_id=NEW.id AND status='active';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER archived_team_memberships_guard
        AFTER UPDATE OF status ON teams
        FOR EACH ROW EXECUTE FUNCTION deactivate_archived_team_memberships();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_team_membership_department()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM teams t
            JOIN user_profiles u ON u.organisation_id=t.organisation_id
            WHERE t.id=NEW.team_id AND u.id=NEW.user_id
              AND t.organisation_id=NEW.organisation_id
              AND t.department_id=u.primary_department_id
              AND t.status='active' AND u.status='active'
          ) THEN
            RAISE EXCEPTION 'active team membership requires an active same-department user and team';
          END IF;
          RETURN NEW;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_team_membership_department()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM teams t JOIN user_profiles u ON u.organisation_id=t.organisation_id
            WHERE t.id=NEW.team_id AND u.id=NEW.user_id
              AND t.organisation_id=NEW.organisation_id
              AND t.department_id=u.primary_department_id
          ) THEN
            RAISE EXCEPTION 'team membership must remain inside the user department';
          END IF;
          RETURN NEW;
        END $$;
        """
    )
    op.execute("DROP TRIGGER archived_team_memberships_guard ON teams")
    op.execute("DROP FUNCTION deactivate_archived_team_memberships()")
    op.execute("DROP TRIGGER active_team_department_guard ON teams")
    op.execute("DROP FUNCTION enforce_active_team_department()")
    op.execute("DROP TRIGGER occupied_department_archive_guard ON departments")
    op.execute("DROP FUNCTION prevent_occupied_department_archive()")
    op.execute("DROP INDEX teams_org_department_name_ci_unique")
    op.execute("ALTER TABLE teams DROP COLUMN version")
    op.execute("ALTER TABLE teams DROP COLUMN updated_at")
