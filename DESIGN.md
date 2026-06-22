# Design Document — Search Typeahead

This explains *why* the system is built the way it is, so every major choice can be
defended in a viva. Read it next to the code; each section names the file that
implements it. Section numbers follow the assignment brief.

---

## 1. Problem & approach

When a user types a prefix we must return the most relevant completions almost
instantly (typeahead must feel real-time). "Relevant" = **most popular** (highest
search count), with an enhanced mode that also rewards **recent** activity. We also
record submitted searches, surface **trending** queries, distribute the **cache** for
low latency, and **batch writes** to protect the database.

The data path is: **Postgres** is the durable source of truth (query → count); an
in-memory **Trie** is the fast suggestion index built from it; a **distributed Redis
cache** sits in front of the Trie; **Redis-meta** holds the recent-activity window;
and a **batch writer** absorbs write load.

---

## 2. Data modeling  (`app/db.py`)

One table:

```sql
search_queries(
  query            TEXT PRIMARY KEY,
  count            BIGINT NOT NULL DEFAULT 0,
  last_searched_at TIMESTAMPTZ
)
```

- `query` as **primary key** → free unique index, and it enables an atomic
  "insert-or-increment" via `ON CONFLICT ... DO UPDATE` (the batch UPSERT).
- `count` is the popularity the suggestions rank by; `BIGINT` because popular queries
  grow large.
- `last_searched_at` aids debugging and could later drive decay.

**Why one table is enough:** suggestions are fully determined by (query, count). The
Trie *is* the precomputed suggestion index and is rebuildable from this table on
startup; trending lives in Redis (short-lived, high-write). No second table needed.

---

## 3. Dataset  (`scripts/generate_dataset.py`, `app/db.py::ingest_csv`)

We generate ≥100,000 unique queries with a **Pareto (heavy-tailed)** count
distribution — a few very popular queries, a long tail of rare ones — which mirrors
real search traffic and makes ranking/trending meaningful. The generator is seeded,
so the dataset is reproducible. Any `query,count` CSV can be substituted; ingestion
lowercases, aggregates duplicates, and bulk-loads in chunks.

---

## 4. The suggestion engine: Trie with cached top-K  (`app/trie.py`)

A **Trie** stores queries along character paths, so every node is a prefix and the
subtree below holds all completions of that prefix.

**Key optimisation:** each node caches the **top candidate pool** of its subtree, so a
query is "walk to the prefix node, read its cached list" → **O(length of prefix)**,
independent of how many queries match. We cache a *pool* (30) slightly larger than the
10 we return, so the recency re-ranking has spare candidates.

**Two build paths:**
- `bulk_build` (startup): insert all words, then compute each node's pool in one
  bottom-up pass. Building 100k queries takes ~2s — far faster than maintaining the
  pool on every insert.
- `insert` (live updates from the batch writer): maintain the pool incrementally along
  the path. Correct because counts only ever increase, so a re-inserted query with a
  higher count simply re-sorts into place; a query that had dropped out can climb back
  in when its count rises.

**Why a Trie and not `... WHERE query LIKE 'pre%'`?** The SQL form scans and sorts at
query time and hits the DB on every keystroke. The Trie keeps the hot path in memory
and exploits the prefix structure. Cost: extra memory for the per-node pools — the
standard space-for-speed trade.

---

## 5. Distributed cache + consistent hashing  (`app/cache_cluster.py`, `app/consistent_hash.py`)

The brief requires the **cache** to be split across multiple logical nodes, with
**consistent hashing choosing which node owns a prefix key**. We run **3 Redis cache
nodes**; a hash ring maps each prefix to exactly one node.

**Read flow (`GET /suggest`):** route the prefix to its owning node → ask only that
node → **hit** returns instantly; **miss** computes from the Trie/Suggester and stores
the result back on that node.

**Why consistent hashing, not `hash(prefix) % N`?** Modulo remaps almost every key
when N changes (3→4 nodes), cold-starting the whole cache. Consistent hashing places
nodes and prefixes on a ring; a prefix is owned by the next node clockwise, so adding
or removing one node moves only the keys in that node's arc — **~1/N** of them. Our
`ch_demo` measures 22.9% remap when going 3→4 nodes (vs ~100% for modulo).

**Virtual nodes (150/node):** without them, single points on the ring give uneven
arcs and one node gets overloaded; many virtual points smooth the distribution
(measured ≈ 34/35/31% across 3 nodes).

**Routing key vs storage key:** we *route* by prefix alone (both ranking modes for a
prefix share a node), but *store* under `suggest:<mode>:<prefix>` so basic and recency
results don't overwrite each other.

**Expiry / invalidation:** every entry has a TTL (basic 60s, recency 5s since it
changes fast). On a batch flush we also actively delete the cached entries for every
prefix of each changed query, so rankings refresh promptly; TTL is the backstop.

**Honest trade-off:** prefixes — not whole queries — are the cache key, so a prefix's
completions are computed once and reused. The Trie itself is not sharded (it fits in
memory for this scale); if it had to scale past one machine we'd shard it too and the
`/suggest` miss path would scatter-gather across Trie shards.

---

## 6. Data storage & caching expectations

All satisfied: query-count data is held reliably in Postgres; suggestions go
**cache → Trie** (cache first, primary index as fallback); the cache stores
**per-prefix** suggestion results; entries **expire** (TTL) and are **invalidated** on
writes; the cache is **distributed across 3 nodes** with **consistent hashing**
owning each prefix key. `GET /cache/debug?prefix=` exposes the owner node and hit/miss
for inspection.

---

## 7. Trending & recency-aware ranking  (`app/activity.py`, `app/suggester.py`)

**Basic ranking (60%):** sort matching queries by **overall count**. The Trie pool is
already sorted this way, so it's "take the top K".

**Recency-aware ranking (enhanced +20%):** combine popularity with recent activity:

```
combined_score = all_time_count + ALPHA * recent_count_in_window     (ALPHA = 50)
```

The four points the brief asks us to explain:

1. **How recent searches are tracked.** Time is bucketed into one-minute Redis sorted
   sets (`recent:<minute>`); each search does `ZINCRBY` on the current minute. "Last W
   minutes" = `ZUNIONSTORE` of the last W buckets.
2. **How recent activity affects ranking.** The `ALPHA * recent_count` term lifts
   queries being searched right now. We *display* the all-time count but *order* by the
   combined score, so a lower-count-but-surging query can sit above a quieter, higher
   count one. (Demonstrated in the logs: "iphone premium for beginners", count 936,
   jumps from rank 8 to rank 5 after 50 recent searches.)
3. **Avoiding permanent over-ranking of brief spikes.** `recent_count` comes from a
   *sliding window*; old buckets expire (TTL), so a spike's boost disappears after the
   window passes. A single running counter could never decay — this is why we window.
4. **How the cache is updated/invalidated when rankings change.** Recency entries use
   a short TTL (5s); plus, on each batch flush we actively invalidate the affected
   queries' prefixes. So changed rankings show up promptly.

**Surfacing brand-new queries:** the recency candidate set is the Trie pool **plus**
recently-hot queries matching the prefix, so a surging query can appear even before its
all-time count climbs into the pool.

**Trade-offs (freshness vs latency vs complexity):** recency mode does a couple of
extra Redis reads and uses a short TTL, trading a little latency/freshness churn for
much fresher results. Basic mode is cheapest and most cacheable. The same `/suggest`
API serves both via `?ranking=`.

The **Trending panel** (`GET /trending`) uses the same window: the top-k of the recent
union.

---

## 8. Batch writes  (`app/batch_writer.py`)

Writing to Postgres on every `/search` means one tiny UPDATE per event — many round
trips and lock contention.

**Mechanism:** `/search` increments an in-memory `Counter` (query → pending count).
The buffer flushes when it holds `BATCH_MAX_SIZE` (100) distinct queries **or** every
`BATCH_FLUSH_INTERVAL_SECONDS` (5s) — whichever first. A flush is **one bulk UPSERT**;
repeated queries are aggregated (20 searches → `+20` in one statement). The DB returns
new totals (`RETURNING`), which we use to update the Trie and invalidate caches.

**Write reduction (measured):** 240 events → **2** DB write statements = **120×**
fewer writes (see `PERFORMANCE.md`).

**Concurrency:** an `asyncio.Lock` guards the buffer; we snapshot-and-swap under the
lock, then do the DB write *outside* it so requests aren't blocked during I/O. The
blocking psycopg2 call runs in `asyncio.to_thread` so the event loop never freezes.

**Failure trade-off (asked for explicitly):** the buffer is in memory, so a crash
before a flush loses at most one interval (~5s) of increments. For approximate,
eventually-consistent popularity counts that's acceptable. For durability we'd persist
the buffer to an append-only log or a queue (e.g. Kafka) and flush from there — the
standard next step.

---

## 9. UI  (`frontend/`)

Search box; suggestion dropdown that updates as you type (**debounced 120ms** to avoid
unnecessary backend calls); submit on **Enter / Search button / clicking a suggestion**;
the dummy `{"message":"Searched"}` response is displayed; a **trending** panel;
**loading & error** states in the status tag; **keyboard navigation** (Arrow keys +
Enter); and a basic/recency toggle so the ranking difference is visible live.

---

## 10. Non-functional

Runs locally with one `docker compose up`. `/suggest` is cache-fronted and in-memory
(p95 ≈ 1.85ms). `/metrics` reports cache hit rate and DB read/write counts;
`scripts/benchmark.py` reports p50/p95/p99; `scripts/ch_demo.py` logs consistent
hashing behaviour. Code is split into one-job modules and documented.

---

## 11. What I'd do next (limitations, honestly stated)

- **Fault tolerance:** the Trie and cache are recoverable from Postgres on restart, but
  a production system would replicate Postgres and the cache nodes and persist the ring.
- **Durable writes:** append-only log in front of the batch buffer (see §8).
- **Fuzzy/typo tolerance:** currently strict prefix match; could add edit-distance.
- **Personalisation:** ranking is global; real systems blend personal history.

These are deliberate scope choices for a university-level build — all five graded
concepts (data modeling, distributed cache + consistent hashing, trending/recency,
batch writes, plus the working UI/APIs) are implemented, measured, and demonstrated.
