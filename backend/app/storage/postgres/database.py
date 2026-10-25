"""Connection pool and schema management for Postgres.

One pool per process. The schema file is idempotent, so applying it at
every startup is how a fresh database (or a new deployment) gets its
tables without a separate migration step.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.exceptions import ConfigurationError

log = logging.getLogger(__name__)


class Database:
    def __init__(
        self,
        url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
        max_idle_seconds: float = 240.0,
    ):
        if not url:
            raise ConfigurationError(
                "DATABASE_URL is not set. LinkChat needs a Postgres database with the pgvector "
                "extension; see backend/.env.example."
            )
        self._url = url
        self._pool = AsyncConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            max_idle=max_idle_seconds,
            open=False,
            # Verify a pooled connection before handing it out, so a socket the
            # server closed while idle is replaced instead of failing a request.
            check=AsyncConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "autocommit": True},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30.0)
        log.info("database pool open (%d-%d connections)", self._pool.min_size, self._pool.max_size)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A connection with autocommit off for the block; commits on success."""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                yield conn

    async def migrate(self, schema_path: Path) -> None:
        sql = schema_path.read_text(encoding="utf-8")
        async with self.connection() as conn:
            await conn.execute(sql)  # the file wraps itself in BEGIN/COMMIT
            row = await (await conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")).fetchone()
        log.info("database schema version %s", row["value"] if row else "unknown")

    async def ping(self) -> bool:
        try:
            async with self.connection() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 - health check
            log.warning("database ping failed", exc_info=True)
            return False
