"""
Recent-activity tracking — the basis of both the "Trending searches" panel and the
recency-aware ranking (the enhanced +20% version of suggestions).

How we track recency
---------------------
We bucket time into one-minute windows. Each bucket is a Redis Sorted Set (ZSET)
keyed by the minute, e.g. `recent:29051234`. When a query is searched we `ZINCRBY`
the *current* minute's bucket, adding 1 to that query's score in that minute. Each
bucket is given a TTL a little past the window, so Redis deletes old minutes for us.

To get activity "in the last W minutes" we `ZUNIONSTORE` the last W bucket keys,
which sums each query's score across them, into a short-lived temp key. From that we
can:
  - read the top-K (the trending list), and
  - look up the recent score of specific candidate queries (for ranking).

Why a sliding window (and not one running counter)?
---------------------------------------------------
This is the answer to "how do we avoid permanently over-ranking a query that was
popular only briefly?". Because we only ever sum *recent* minutes, a short spike's
contribution falls out of the window after W minutes and the query naturally stops
trending. A single running counter could never decay, so old spikes would linger
forever.
"""

import time
from typing import Dict, List, Tuple

import redis.asyncio as redis


class Activity:
    def __init__(self, host: str, port: int, window_minutes: int) -> None:
        self.client = redis.Redis(host=host, port=port, decode_responses=True)
        self.window_minutes = window_minutes

    @staticmethod
    def _now_minute() -> int:
        return int(time.time() // 60)

    def _bucket(self, minute: int) -> str:
        return f"recent:{minute}"

    async def record(self, query: str) -> None:
        """Count one search for `query` in the current minute's bucket."""
        key = self._bucket(self._now_minute())
        await self.client.zincrby(key, 1, query)
        await self.client.expire(key, (self.window_minutes + 1) * 60)

    async def _build_window(self) -> str:
        """Union the last W minute-buckets into a temp key; return that key."""
        now = self._now_minute()
        keys = [self._bucket(now - i) for i in range(self.window_minutes)]
        dest = f"recent:window:{now}"
        await self.client.zunionstore(dest, keys)
        await self.client.expire(dest, 10)  # short-lived; refreshed each call
        return dest

    async def trending(self, k: int) -> List[Tuple[str, int]]:
        """Top-k queries across the recent window (for the Trending panel)."""
        dest = await self._build_window()
        pairs = await self.client.zrevrange(dest, 0, k - 1, withscores=True)
        return [(q, int(s)) for q, s in pairs]

    async def recent_counts(self, queries: List[str]) -> Dict[str, int]:
        """Recent-window score for each given query (0 if none). Used by ranking."""
        if not queries:
            return {}
        dest = await self._build_window()
        scores = await self.client.zmscore(dest, queries)
        return {q: int(s) for q, s in zip(queries, scores) if s is not None}

    async def recent_matching_prefix(self, prefix: str, limit: int = 200) -> List[Tuple[str, int]]:
        """Recently-hot queries that start with `prefix`.

        This lets a *surging* query appear in suggestions even if its all-time count
        hasn't yet climbed into the Trie's candidate pool.
        """
        dest = await self._build_window()
        pairs = await self.client.zrevrange(dest, 0, limit - 1, withscores=True)
        return [(q, int(s)) for q, s in pairs if q.startswith(prefix)]

    async def ping(self) -> bool:
        return await self.client.ping()

    async def close(self) -> None:
        await self.client.aclose()
