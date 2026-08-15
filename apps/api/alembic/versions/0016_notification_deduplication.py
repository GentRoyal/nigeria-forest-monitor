"""Add notification deduplication keys.

Revision ID: 0016_notify_dedupe
Revises: 0015_notify_prefs
"""

from alembic import op

revision = "0016_notify_dedupe"
down_revision = "0015_notify_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN dedupe_key text")
    op.execute("CREATE UNIQUE INDEX notifications_recipient_dedupe_idx ON notifications(organisation_id,recipient_id,dedupe_key) WHERE dedupe_key IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX notifications_recipient_dedupe_idx")
    op.execute("ALTER TABLE notifications DROP COLUMN dedupe_key")
