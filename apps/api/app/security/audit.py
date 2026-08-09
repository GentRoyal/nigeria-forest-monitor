from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


async def record_audit(
    connection: AsyncConnection,
    *,
    organisation_id: UUID,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: UUID | None = None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    reason: str | None = None,
    ip_address: str | None = None,
) -> None:
    await connection.execute(
        """
        INSERT INTO audit_events (
          organisation_id, actor_id, action, target_type, target_id,
          before_summary, after_summary, reason, ip_address
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            organisation_id,
            actor_id,
            action,
            target_type,
            target_id,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
            reason,
            ip_address,
        ),
    )
