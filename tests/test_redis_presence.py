from __future__ import annotations

import asyncio

import pytest

from logic.redis_presence import RedisSocketPresence


class FakeRedis:
    def __init__(self) -> None:
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    async def zadd(self, key: str, members: dict[str, float]) -> None:
        self.sorted_sets.setdefault(key, {}).update(members)

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds

    async def zremrangebyscore(self, key: str, minimum: str, maximum: float) -> None:
        members = self.sorted_sets.get(key, {})
        self.sorted_sets[key] = {
            member: score for member, score in members.items() if score > maximum
        }

    async def zrangebyscore(self, key: str, minimum: float, maximum: str) -> list[str]:
        return list(self.sorted_sets.get(key, {}))

    async def zrem(self, key: str, member: str) -> None:
        self.sorted_sets.get(key, {}).pop(member, None)

    async def zcard(self, key: str) -> int:
        return len(self.sorted_sets.get(key, {}))

    async def delete(self, key: str) -> None:
        self.sorted_sets.pop(key, None)

    async def aclose(self) -> None:
        return None


def test_presence_tracks_gateway_per_user_and_expires_with_ttl(monkeypatch) -> None:
    async def verify() -> None:
        now = 100.0
        monkeypatch.setattr("logic.redis_presence.time.time", lambda: now)
        redis = FakeRedis()
        first = RedisSocketPresence(redis, "gateway-a", ttl_seconds=30)
        second = RedisSocketPresence(redis, "gateway-b", ttl_seconds=30)

        await first.touch("club:club-1", "table-1", "player-1")
        await second.touch("club:club-1", "table-1", "player-1")
        assert await first.gateways_for_user("club:club-1", "table-1", "player-1") == [
            "gateway-a",
            "gateway-b",
        ]
        assert redis.expirations["table-presence:club:club-1:table-1:player-1"] == 60

        await first.remove("club:club-1", "table-1", "player-1")
        assert await first.gateways_for_user("club:club-1", "table-1", "player-1") == ["gateway-b"]

        now += 31
        assert await second.gateways_for_user("club:club-1", "table-1", "player-1") == []

    asyncio.run(verify())


def test_presence_requires_stable_gateway_and_sufficient_ttl() -> None:
    with pytest.raises(ValueError):
        RedisSocketPresence(FakeRedis(), "", ttl_seconds=30)
    with pytest.raises(ValueError):
        RedisSocketPresence(FakeRedis(), "gateway-a", ttl_seconds=9)
