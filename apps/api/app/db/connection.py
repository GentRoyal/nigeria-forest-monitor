import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ..settings import get_settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def system_connection() -> AsyncIterator[AsyncConnection]:
    """Create a transaction without tenant access (migrations/global tables only)."""
    connection = await psycopg.AsyncConnection.connect(
        get_settings().database_url,
        connect_timeout=5,
        row_factory=dict_row,
    )
    try:
        async with connection.transaction():
            yield connection
    finally:
        await connection.close()


@asynccontextmanager
async def tenant_connection(
    organisation_id: UUID,
    user_id: UUID | None = None,
) -> AsyncIterator[AsyncConnection]:
    """Create a transaction whose database-visible rows are restricted by RLS."""
    connection = await psycopg.AsyncConnection.connect(
        get_settings().database_url,
        connect_timeout=5,
        row_factory=dict_row,
    )
    try:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.current_organisation_id', %s, true)",
                (str(organisation_id),),
            )
            await connection.execute(
                "SELECT set_config('app.current_user_id', %s, true)",
                (str(user_id) if user_id else "",),
            )
            yield connection
    finally:
        await connection.close()
