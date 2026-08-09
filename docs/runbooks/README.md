# Operations runbooks

Create one Markdown file per operational procedure. Every runbook must include:

1. purpose, owner, scope, and last-reviewed date;
2. symptoms and objective trigger conditions;
3. safe diagnostics that do not mutate data;
4. containment and recovery steps with exact commands;
5. destructive-step warnings and backup prerequisites;
6. verification, rollback, and escalation criteria;
7. evidence to retain for the audit trail; and
8. follow-up actions and links to the related architecture decision.

Initial runbooks will cover failed Airflow jobs, stale monitoring schedules,
raster-storage exhaustion, database backup/restore, and compromised credentials.
