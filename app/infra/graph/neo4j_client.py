import logging
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

logger = logging.getLogger(__name__)


class Neo4jClient:
    def __init__(self, uri: str, username: str, password: str):
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            uri,
            auth=(username, password),
        )

    async def run(self, query: str, **params) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(query, **params)
            return await result.data()

    async def run_write(self, query: str, **params) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(query, **params)
            return await result.data()

    async def ping(self) -> bool:
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error(f"[Neo4j] Ping falhou: {e}")
            return False

    async def close(self):
        await self._driver.close()
