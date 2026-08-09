import psycopg

from .settings import get_settings


async def database_is_ready() -> None:
    """Open a short-lived connection and fail if PostgreSQL is unavailable."""
    settings = get_settings()
    connection = await psycopg.AsyncConnection.connect(settings.database_url, connect_timeout=3)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT 1")
            await cursor.fetchone()
    finally:
        await connection.close()
