from uuid import UUID

from psycopg import AsyncConnection


async def notify_event_subscribers(
    connection: AsyncConnection,
    *,
    organisation_id: UUID,
    site_id: UUID,
    event_id: UUID,
    notification_type: str,
    safe_summary: str,
    sensitivity: str,
    protected_path: str,
    explicit_recipient_id: UUID | None = None,
) -> int:
    """Create deduplicated notification and outbox rows for authorised recipients."""
    recipients = await (await connection.execute(
        """SELECT DISTINCT u.id,u.notification_preferences,
          COALESCE(s.channels,'[]'::jsonb) subscription_channels
        FROM user_profiles u
        LEFT JOIN subscriptions s ON s.user_id=u.id AND (s.event_id=%s OR s.site_id=%s)
        WHERE u.status='active' AND (s.id IS NOT NULL OR u.id=%s)""",
        (event_id, site_id, explicit_recipient_id),
    )).fetchall()
    created = 0
    for recipient in recipients:
        preferences = recipient["notification_preferences"] or {"channels": ["in_app"]}
        channels = set(preferences.get("channels", []))
        subscribed = set(recipient["subscription_channels"] or [])
        if recipient["id"] != explicit_recipient_id:
            channels &= subscribed
        if not channels:
            continue
        dedupe_key = f"{notification_type}:{event_id}"
        notification = await (await connection.execute(
            """INSERT INTO notifications(organisation_id,recipient_id,event_id,notification_type,safe_summary,sensitivity,protected_path,dedupe_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (organisation_id,recipient_id,dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            RETURNING id""",
            (organisation_id, recipient["id"], event_id, notification_type, safe_summary, sensitivity, protected_path, dedupe_key),
        )).fetchone()
        if not notification:
            continue
        created += 1
        for channel in channels:
            await connection.execute(
                """INSERT INTO notification_deliveries(organisation_id,notification_id,channel,destination_reference,status)
                VALUES (%s,%s,%s,%s,'pending')""",
                (organisation_id, notification["id"], channel, "local-outbox" if channel == "email" else None),
            )
    return created
