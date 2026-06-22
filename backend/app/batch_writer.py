"""
The batch writer absorbs the write load from search submissions.

The problem
-----------
Every /search submission must bump a query's count in Postgres. Writing to the DB on
every single submission means one tiny UPDATE per event — many round trips and lock
contention under load.

The fix: batching
-----------------
/search just increments an in-memory `Counter` (query -> pending count). The buffer is
flushed when **either** it holds `BATCH_MAX_SIZE` distinct queries **or**
`BATCH_FLUSH_INTERVAL_SECONDS` has passed — whichever first. A flush is a single bulk
UPSERT: if a query was searched 20× between flushes, that's `+20` in one statement
instead of 20 statements. After the DB returns the new totals we (a) update the Trie
so rankings reflect the new counts and (b) invalidate the cached entries for the
affected queries' prefixes.

Failure trade-off (asked for explicitly in the brief)
------------------------------------------------------
The buffer lives in memory. If the process crashes *before* a flush, the increments
collected since the last flush are lost — at most `BATCH_FLUSH_INTERVAL_SECONDS` worth
(plus whatever a partial batch held). For search-popularity counts — an approximate,
eventually-consistent metric — that's an acceptable price for far fewer, larger
writes. If we needed durability we'd write the buffer to an append-only log (or a
queue like Kafka) first and flush from there; that's the standard next step.

Concurrency
-----------
Many requests call `add()` concurrently while a background task flushes on a timer. An
asyncio.Lock guards the shared buffer; we snapshot-and-swap it under the lock, then do
the DB write *outside* the lock so requests aren't blocked during I/O. The blocking
psycopg2 call runs in `asyncio.to_thread` so it never freezes the event loop.
"""

import asyncio
from collections import Counter
from typing import List

from . import config, db, metrics
from .cache_cluster import CacheCluster
from .trie import Trie


class BatchWriter:
    def __init__(self, trie: Trie, cache: CacheCluster, modes: List[str]) -> None:
        self.trie = trie
        self.cache = cache
        self.modes = modes
        self._buffer: Counter = Counter()
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._stopping = False

    async def add(self, query: str) -> None:
        """Record one search submission. Cheap: just bump an in-memory counter."""
        metrics.search_events += 1
        async with self._lock:
            self._buffer[query] += 1
            should_flush = len(self._buffer) >= config.BATCH_MAX_SIZE
        if should_flush:
            await self.flush()

    async def flush(self) -> int:
        """Write buffered increments to the DB, then refresh Trie + cache."""
        async with self._lock:
            if not self._buffer:
                return 0
            increments = dict(self._buffer)
            self._buffer = Counter()

        # One bulk UPSERT for the whole batch (psycopg2 is blocking -> run in a thread).
        new_totals = await asyncio.to_thread(db.bulk_upsert, increments)
        metrics.batch_flushes += 1

        for query, total in new_totals:
            self.trie.insert(query, total)              # keep rankings current
            await self.cache.invalidate_query(query, self.modes)  # drop stale cached prefixes

        return len(increments)

    async def _run_periodic_flush(self) -> None:
        while not self._stopping:
            await asyncio.sleep(config.BATCH_FLUSH_INTERVAL_SECONDS)
            try:
                await self.flush()
            except Exception as exc:  # never let one bad flush kill the loop
                print(f"[batch_writer] periodic flush failed: {exc}")

    def start(self) -> None:
        self._stopping = False
        self._flush_task = asyncio.create_task(self._run_periodic_flush())

    async def stop(self) -> None:
        """Stop the timer and flush what's left, so we don't lose buffered writes."""
        self._stopping = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()

    def pending(self) -> int:
        return len(self._buffer)
