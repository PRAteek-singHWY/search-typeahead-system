# Performance Report

All numbers below were measured on the running stack (FastAPI + 3 Redis cache nodes +
Redis-meta + Postgres) with a **100,000-query** dataset. Reproduce them yourself with
the scripts in `backend/scripts/`.

## 1. Suggestion latency (`GET /suggest`)

Measured with `python -m scripts.benchmark 3000`, which fires 3,000 requests across a
realistic spread of prefixes (repeats included so the cache warms).

| Metric | Value |
|--------|-------|
| Requests | 3,000 |
| Average  | **1.38 ms** |
| p50      | 1.25 ms |
| **p95**  | **1.85 ms** |
| p99      | 3.50 ms |
| Max      | 19.0 ms |

Sub-2ms p95 — the in-memory Trie plus the distributed cache keep reads fast.

## 2. Cache hit rate

From the same run (`GET /metrics`):

| Metric | Value |
|--------|-------|
| Cache hits | 2,965 |
| Cache misses | 35 |
| **Hit rate** | **98.8%** |

The 35 misses are the first time each distinct prefix is seen (cold cache); every
repeat is served from the owning cache node. This is the cache doing its job: popular
prefixes almost never hit the Trie a second time within the TTL.

## 3. Database read/write counts

- **Reads:** 2 total — both at startup (count check + full load to build the Trie).
  Serving suggestions never reads the database; it reads the cache or the in-memory
  Trie. This is the whole point of the caching design.
- **Writes:** see below — writes only happen on batch flushes, never per request.

## 4. Batch-write reduction

Measured by firing **240 search submissions** across 5 distinct queries
(`POST /search`) and reading `GET /metrics`:

| Metric | Value |
|--------|-------|
| Search events received | 240 |
| Batch flushes (DB write statements) | 2 |
| Rows written | 8 |
| **Write-reduction factor** (events ÷ write calls) | **120×** |

Without batching we would have issued **240** individual UPDATE statements. With
batching we issued **2** bulk UPSERTs — a 120× reduction in database write
operations. The reduction grows with traffic and with how often the same query
repeats (repeats are aggregated in the buffer before the flush).

**Failure trade-off:** the buffer is in memory, so a crash before a flush loses at
most one flush interval (default 5s) of increments. For approximate popularity counts
that is acceptable; durability would come from writing the buffer to an append-only
log or queue first (see `DESIGN.md` §8).

## 5. Consistent-hashing behaviour

From `python -m scripts.ch_demo` (10,000 prefixes, 150 virtual nodes/node):

```
Distribution across 3 nodes:
  cache-0: 34.0%   cache-1: 35.5%   cache-2: 30.5%

After adding cache-3:
  cache-0: 26.4%   cache-1: 27.6%   cache-2: 23.1%   cache-3: 22.9%

Prefixes remapped: 22.9%
```

Adding a 4th node moved only **22.9%** of prefixes (≈ 1/4). Plain `hash % N` would
have remapped close to 100%, cold-starting the entire cache. This is the property
that makes the cache layer safely scalable.
