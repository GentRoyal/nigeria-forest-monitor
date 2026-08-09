# Data retention, deletion, backup, and restore

This policy implements the approved Phase 3 local data-governance contract. It
applies to product data in the forest_monitor database and raster objects under
the configured storage root. Airflow metadata is operational state in the
separate airflow database and is never the product source of truth.

## Retention

| Record or object | Default | Extension rule | End-of-life action |
|---|---:|---|---|
| Source catalogue references | While provenance depends on them | Any dependent observation/run | Retain minimum source identity and licence |
| Derived COGs and thumbnails | 2 years | Unresolved event or active retention hold | Delete object; retain checksum/lineage tombstone |
| Events, reviews, evidence metadata, processing runs | 7 years | Active retention hold | Delete or anonymise personal fields as authorised |
| Audit events | 7 years | Active retention hold | Archive then delete only through controlled maintenance |
| Notification delivery attempts | 1 year | None by default | Delete delivery metadata |
| Completed exports | Until expires_at | Active retention hold | Delete object and expire database record |
| Authentication sessions | Expiry plus 30 days | Security investigation hold | Delete device/IP metadata; retain audit event |

Retention time is calculated in UTC. A retention worker must select candidates
inside one organisation RLS context, recheck unresolved-event links and active
holds in the same transaction, delete object bytes first, and commit database
tombstones only after object deletion succeeds. A failed object deletion is
retryable and must not erase lineage.

## Site deletion

Site deletion is always a soft deletion first:

1. An owner or administrator supplies a reason.
2. The site becomes deleted, monitoring stops, and deleted_at plus
   recoverable_until = deleted_at + 30 days are recorded.
3. Existing jobs are not silently cancelled; job cancellation is separate.
4. During 30 days an administrator may restore the site without losing history.
5. After 30 days, a retention job checks holds and unresolved events, deletes
   permitted objects/content, and keeps the minimum audit/provenance tombstone.

Organisation deletion is not an MVP self-service action. It requires an
authorised export decision, hold review, backup, two-person approval, and an
operator runbook added before hosted multi-tenant release.

## Anonymisation

Disabling a user never rewrites submitted reviews or audit authorship. When an
authorised erasure request applies, display name, email, device metadata, and IP
addresses may be replaced with stable pseudonymous values after hold review.
Stable user IDs remain on immutable audit and provenance records. Institutional
evidence follows the customer's records authority rather than ordinary account
deletion.

## Backup and restore

scripts/backup-database.ps1 creates a timestamped PostgreSQL custom-format dump
under backups/, which is git-ignored. It covers the product database only;
TIFF/COG objects require a separate copy of the raster storage root. Real
environments must encrypt backups, store them off-machine, restrict access, and
record restore-test results.

scripts/restore-database.ps1 requires the exact backup path and an explicit
confirmation phrase. It overwrites only the local product database, runs current
migrations afterward, and never touches Airflow metadata or rasters. Validate
API readiness, migration revision, tenant isolation, grid counts, audit
immutability, and object checksums after every restore.

Never restore one organisation into another organisation's live workspace.
Organisation-level portability must use a future audited import/export workflow.
