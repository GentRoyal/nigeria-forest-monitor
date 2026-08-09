"""Add optimistic concurrency metadata for member administration.

Revision ID: 0006_member_resources
Revises: 0005_membership_lifecycle_fix
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_member_resources"
down_revision: str | None = "0005_membership_lifecycle_fix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_profiles ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0)"
    )
    op.execute(
        "CREATE INDEX member_directory_idx "
        "ON user_profiles (organisation_id,status,role,primary_department_id)"
    )
    op.execute(
        """
        CREATE FUNCTION enforce_member_administration_invariants()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE actor_role text;
        BEGIN
          IF OLD.primary_department_id<>NEW.primary_department_id AND EXISTS (
            SELECT 1 FROM team_memberships tm
            JOIN teams t ON t.id=tm.team_id AND t.organisation_id=tm.organisation_id
            WHERE tm.user_id=OLD.id AND tm.status='active'
              AND t.department_id<>NEW.primary_department_id
          ) THEN
            RAISE EXCEPTION 'incompatible team memberships must be deactivated first';
          END IF;
          IF OLD.role='owner' OR NEW.role='owner' THEN
            SELECT role INTO actor_role FROM user_profiles
            WHERE id=app_current_user_id() AND status='active';
            IF actor_role IS DISTINCT FROM 'owner' THEN
              RAISE EXCEPTION 'only an active owner may modify the owner role';
            END IF;
          END IF;
          IF OLD.role='owner' AND OLD.status='active'
             AND (NEW.role<>'owner' OR NEW.status<>'active')
             AND (SELECT count(*) FROM user_profiles
                  WHERE role='owner' AND status='active')<=1 THEN
            RAISE EXCEPTION 'the last active owner cannot be disabled, suspended, or demoted';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER member_administration_invariants_guard
        BEFORE UPDATE OF role,status,primary_department_id ON user_profiles
        FOR EACH ROW EXECUTE FUNCTION enforce_member_administration_invariants();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER member_administration_invariants_guard ON user_profiles")
    op.execute("DROP FUNCTION enforce_member_administration_invariants()")
    op.execute("DROP INDEX member_directory_idx")
    op.execute("ALTER TABLE user_profiles DROP COLUMN version")
