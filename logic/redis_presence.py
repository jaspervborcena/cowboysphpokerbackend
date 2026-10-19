from __future__ import annotations

import time
from typing import Any


class RedisSocketPresence:
    def __init__(self, client: Any, gateway_id: str, ttl_seconds: int = 120) -> None:
        if not gateway_id or ttl_seconds < 10:
            raise ValueError("Gateway ID and a presence TTL of at least 10 seconds are required.")
        self._client = client
        self.gateway_id = gateway_id
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _key(scope: str, table_id: str, user_id: str) -> str:
        if not scope or not table_id or not user_id:
            raise ValueError("Table scope, table ID, and user ID are required.")
        return f"table-presence:{scope}:{table_id}:{user_id}"

    async def touch(self, scope: str, table_id: str, user_id: str) -> None:
        expires_at = time.time() + self.ttl_seconds
        key = self._key(scope, table_id, user_id)
        await self._client.zadd(key, {self.gateway_id: expires_at})
        await self._client.expire(key, self.ttl_seconds * 2)

    async def gateways_for_user(self, scope: str, table_id: str, user_id: str) -> list[str]:
        key = self._key(scope, table_id, user_id)
        now = time.time()
        await self._client.zremrangebyscore(key, "-inf", now)
        values = await self._client.zrangebyscore(key, now, "+inf")
        return [value.decode("utf-8") if isinstance(value, bytes) else str(value) for value in values]

    async def remove(self, scope: str, table_id: str, user_id: str) -> None:
        key = self._key(scope, table_id, user_id)
        await self._client.zrem(key, self.gateway_id)
        if await self._client.zcard(key) == 0:
            await self._client.delete(key)

    async def close(self) -> None:
        await self._client.aclose()


def redis_presence_from_url(url: str, gateway_id: str, ttl_seconds: int = 120) -> RedisSocketPresence:
    from redis.asyncio import Redis

    client = Redis.from_url(url, decode_responses=True)
    return RedisSocketPresence(client, gateway_id, ttl_seconds)
