from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.types import utcnow
from app.db.models import KV, Base


class Database:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    async def close(self) -> None:
        await self.engine.dispose()

    async def kv_get(self, key: str, default: dict | None = None) -> dict | None:
        async with self.session() as s:
            row = (await s.execute(select(KV).where(KV.key == key))).scalar_one_or_none()
            return row.value if row else default

    async def kv_set(self, key: str, value: dict) -> None:
        # Atomic upsert: several engine loops may write the same key at the same moment.
        stmt = sqlite_insert(KV).values(key=key, value=value, updated_ts=utcnow())
        stmt = stmt.on_conflict_do_update(index_elements=[KV.key], set_={"value": value, "updated_ts": utcnow()})
        async with self.session() as s:
            await s.execute(stmt)
            await s.commit()

    async def kv_delete(self, key: str) -> None:
        async with self.session() as s:
            row = (await s.execute(select(KV).where(KV.key == key))).scalar_one_or_none()
            if row:
                await s.delete(row)
                await s.commit()
