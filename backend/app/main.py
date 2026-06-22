"""
The FastAPI application: HTTP endpoints + startup/shutdown wiring.

Startup (see `lifespan`):
  1. Connect Postgres; create the table if needed.
  2. If the table is empty, ingest the dataset CSV (generate it first if missing).
  3. Load every (query, count) and bulk-build the in-memory Trie.
  4. Connect the distributed cache cluster + the Activity (recent/trending) Redis,
     and start the batch-writer flush loop.

Endpoints (paths match the assignment's API table):
  GET  /suggest?q=&ranking=&limit=     -> prefix suggestions (cache -> Trie)
  POST /search   {query}               -> records the search, returns {"message":"Searched"}
  GET  /cache/debug?prefix=            -> which cache node owns a prefix + hit/miss
  GET  /trending?k=                    -> what's being searched right now
  GET  /metrics                        -> cache hit rate, DB read/write counts, write reduction
  GET  /stats                          -> config + dataset size
  GET  /                               -> the web UI
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, metrics
from .activity import Activity
from .batch_writer import BatchWriter
from .cache_cluster import CacheCluster
from .suggester import Suggester
from .trie import Trie

MODES = ["basic", "recency"]  # the two ranking modes the cache keys/invalidation cover

state: dict = {}
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def _ranking_ttl(mode: str) -> int:
    return config.CACHE_TTL_RECENCY if mode == "recency" else config.CACHE_TTL_BASIC


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Database
    db.init_pool()
    db.create_schema()

    # 2. Seed the dataset if the table is empty.
    if db.count_rows() == 0:
        if not os.path.exists(config.DATASET_PATH):
            from scripts.generate_dataset import generate
            generate(config.DATASET_PATH, config.DATASET_SIZE)
        ingested = db.ingest_csv(config.DATASET_PATH)
        print(f"[startup] ingested {ingested} queries from {config.DATASET_PATH}")

    # 3. Build the in-memory Trie from the source of truth.
    trie = Trie(pool=config.POOL_K)
    rows = db.load_all()
    trie.bulk_build(rows)
    print(f"[startup] built Trie with {trie.size()} queries")

    # 4. Distributed cache + activity + batch writer + suggester.
    cache = CacheCluster(config.CACHE_NODES, virtual_nodes=config.VIRTUAL_NODES)
    activity = Activity(config.META_HOST, config.META_PORT, config.RECENT_WINDOW_MINUTES)
    suggester = Suggester(trie, activity, alpha=config.RECENCY_ALPHA)
    batch_writer = BatchWriter(trie, cache, modes=MODES)
    batch_writer.start()
    print(f"[startup] cache nodes: {[nid for nid, _, _ in config.CACHE_NODES]}")

    state.update(trie=trie, cache=cache, activity=activity,
                 suggester=suggester, batch_writer=batch_writer)

    yield

    await batch_writer.stop()
    await cache.close()
    await activity.close()
    db.close_pool()


app = FastAPI(title="Search Typeahead", lifespan=lifespan)


class SearchBody(BaseModel):
    query: str


@app.get("/suggest")
async def suggest(
    q: str = Query(default=""),
    ranking: str = Query(default=config.DEFAULT_RANKING),
    limit: int = Query(default=config.TOP_K),
):
    """Return up to `limit` prefix-matching suggestions, sorted by the chosen ranking.

    Cache-first: route the prefix to its owning cache node; on a miss, compute from the
    Trie (basic) or Trie+recency (recency) and store the result back on that node.
    Handles empty/missing/mixed-case/no-match input gracefully.
    """
    prefix = q.strip().lower()
    mode = ranking if ranking in MODES else "basic"
    limit = max(1, min(limit, config.TOP_K))
    if not prefix:
        return {"query": prefix, "ranking": mode, "source": "empty", "suggestions": []}

    cache: CacheCluster = state["cache"]
    suggester: Suggester = state["suggester"]

    cached = await cache.get(mode, prefix)
    if cached is not None:
        suggestions, source = cached[:limit], "cache"
    else:
        suggestions = await suggester.suggest(prefix, mode, limit)
        await cache.set(mode, prefix, suggestions, ttl=_ranking_ttl(mode))
        source = "store"

    return {
        "query": prefix,
        "ranking": mode,
        "source": source,                       # "cache" or "store" — visible in the demo
        "owner_node": cache.node_for(prefix),    # which cache node served/owns this prefix
        "suggestions": [{"query": t, "count": c} for t, c in suggestions],
    }


@app.post("/search")
async def search(body: SearchBody):
    """Record a submitted search and return the dummy response the brief specifies.

    The write is buffered (batch writer) and the query feeds the recent-activity
    window. We do NOT write to Postgres synchronously here, so this stays fast.
    """
    query = body.query.strip().lower()
    if query:
        await state["batch_writer"].add(query)
        await state["activity"].record(query)
    return {"message": "Searched"}


@app.get("/cache/debug")
async def cache_debug(prefix: str = Query(default="")):
    """Show which cache node owns a prefix and whether it's currently cached."""
    prefix = prefix.strip().lower()
    return await state["cache"].debug(prefix, MODES)


@app.get("/trending")
async def trending(k: int = Query(default=config.TOP_K)):
    """Top-k queries searched within the recent window."""
    top = await state["activity"].trending(k)
    return {"trending": [{"query": q, "count": c} for q, c in top]}


@app.get("/metrics")
async def get_metrics():
    """Cache hit rate, DB read/write counts, and batch write-reduction factor."""
    snap = metrics.snapshot()
    snap["pending_in_buffer"] = state["batch_writer"].pending()
    return snap


@app.get("/stats")
async def stats():
    trie: Trie = state["trie"]
    return {
        "dataset_queries": trie.size(),
        "cache_nodes": [nid for nid, _, _ in config.CACHE_NODES],
        "virtual_nodes_per_node": config.VIRTUAL_NODES,
        "top_k": config.TOP_K,
        "candidate_pool_k": config.POOL_K,
        "cache_ttl_basic_seconds": config.CACHE_TTL_BASIC,
        "cache_ttl_recency_seconds": config.CACHE_TTL_RECENCY,
        "recency_alpha": config.RECENCY_ALPHA,
        "recent_window_minutes": config.RECENT_WINDOW_MINUTES,
        "batch_max_size": config.BATCH_MAX_SIZE,
        "batch_flush_interval_seconds": config.BATCH_FLUSH_INTERVAL_SECONDS,
    }


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
