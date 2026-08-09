"""Allow inactive memberships while preserving active membership guards.

Revision ID: 0005_membership_lifecycle_fix
Revises: 0004_team_resources
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_membership_lifecycle_fix"
down_revision: str | None = "0004_team_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
          ) THEN
            RAISE EXCEPTION 'team membership must remain inside the user department';
          END IF;
          IF NEW.status='active' AND NOT EXISTS (
            SELECT 1 FROM teams t
            JOIN user_profiles u ON u.organisation_id=t.organisation_id
            WHERE t.id=NEW.team_id AND u.id=NEW.user_id
              AND t.organisation_id=NEW.organisation_id
              AND t.status='active' AND u.status='active'
          ) THEN
            RAISE EXCEPTION 'active team membership requires an active user and team';
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
