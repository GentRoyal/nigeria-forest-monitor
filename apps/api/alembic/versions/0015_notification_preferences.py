"""Add per-user notification preferences.

Revision ID: 0015_notify_prefs
Revises: 0014_stage_failure
"""

from alembic import op

revision = "0015_notify_prefs"
down_revision = "0014_stage_failure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_profiles ADD COLUMN notification_preferences jsonb NOT NULL "
        "DEFAULT jsonb_build_object('channels',jsonb_build_array('in_app'),'digest_enabled',true)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE user_profiles DROP COLUMN notification_preferences")
