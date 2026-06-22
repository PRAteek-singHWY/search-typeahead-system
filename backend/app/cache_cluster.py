"""
The distributed cache.

The assignment requires the cache layer to be spread across multiple logical cache
nodes, with **consistent hashing deciding which node owns a given prefix key**. So
this is not one Redis — it's several Redis nodes, and a consistent-hash ring routes
each prefix to exactly one of them.

Read flow for a prefix:
  1. ring.get_node(prefix) -> which cache node owns this prefix.
  2. ask only that node for the cached suggestions.
  3. HIT  -> return them (fast, no Trie work).
  4. MISS -> the caller computes suggestions and calls `set`, which stores them back
             on the same owning node.

Cache key vs. routing key
-------------------------
We *route* by the prefix alone (so both ranking modes for "iph" live on the same
node), but we *store* under a key that includes the ranking mode, so the basic and
recency-aware results for the same prefix don't overwrite each other.

Expiry / invalidation
----------------------
- Every entry has a TTL, so stale suggestions can't live forever.
- When a query's count changes (on a batch flush) we actively delete the cached
  entries for all of that query's prefixes, so rankings refresh promptly. TTL is the
  backstop; active invalidation is the prompt path.
"""

import json
from typing import List, Optional, Tuple

import redis.asyncio as redis

from . import metrics
from .consistent_hash import ConsistentHashRing

Suggestion = Tuple[str, int]


class CacheCluster:
    def __init__(self, nodes: List[Tuple[str, str, int]], virtual_nodes: int) -> None:
        # nodes: list of (node_id, host, port)
        self.clients = {
            node_id: redis.Redis(host=host, port=port, decode_responses=True)
            for node_id, host, port in nodes
        }
        self.ring = ConsistentHashRing(list(self.clients.keys()), virtual_nodes=virtual_nodes)

    @staticmethod
    def _key(mode: str, prefix: str) -> str:
        return f"suggest:{mode}:{prefix}"

    def node_for(self, prefix: str) -> str:
        """Which cache node owns this prefix (consistent hashing)."""
        return self.ring.get_node(prefix)

    async def get(self, mode: str, prefix: str) -> Optional[List[Suggestion]]:
        """Return cached suggestions for (mode, prefix), or None on a miss."""
        client = self.clients[self.node_for(prefix)]
        raw = await client.get(self._key(mode, prefix))
        if raw is None:
            metrics.cache_misses += 1
            return None
        metrics.cache_hits += 1
        return [(item[0], item[1]) for item in json.loads(raw)]

    async def set(self, mode: str, prefix: str, suggestions: List[Suggestion], ttl: int) -> None:
        client = self.clients[self.node_for(prefix)]
        await client.set(self._key(mode, prefix), json.dumps(suggestions), ex=ttl)

    async def invalidate_query(self, query: str, modes: List[str], max_prefix_len: int = 25) -> None:
        """Delete cached entries for every prefix of `query`, on each prefix's owner.

        When a query's count changes, any prefix of it could now have a different
        ranking, so we drop those cached results. We cap the prefix length to keep the
        work bounded for very long queries.
        """
        for length in range(1, min(len(query), max_prefix_len) + 1):
            prefix = query[:length]
            client = self.clients[self.node_for(prefix)]
            for mode in modes:
                await client.delete(self._key(mode, prefix))

    async def debug(self, prefix: str, modes: List[str]) -> dict:
        """Which node owns this prefix, and is it currently cached (hit) or not?"""
        node_id = self.node_for(prefix)
        client = self.clients[node_id]
        present = {mode: bool(await client.exists(self._key(mode, prefix))) for mode in modes}
        return {
            "prefix": prefix,
            "owner_node": node_id,
            "cached": present,                      # per ranking mode: hit (True) / miss (False)
            "all_nodes": list(self.clients.keys()),
        }

    async def ping_all(self) -> dict:
        return {nid: await c.ping() for nid, c in self.clients.items()}

    async def close(self) -> None:
        for c in self.clients.values():
            await c.aclose()
