"""
All tunable settings in one place, read from environment variables so the same
code runs locally and inside Docker without edits. Each value has a sensible
default for local runs.
"""

import os
from typing import List, Tuple


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


# --- Postgres (the durable source of truth for query counts) ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = _int("DB_PORT", 5432)
DB_NAME = os.getenv("DB_NAME", "typeahead")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

# --- Distributed cache: several logical cache nodes ---
# A comma-separated list of host:port. Consistent hashing maps a prefix -> node.
_DEFAULT_CACHE_NODES = "localhost:6379"
CACHE_NODES: List[Tuple[str, str, int]] = []
for i, hp in enumerate(os.getenv("CACHE_NODES", _DEFAULT_CACHE_NODES).split(",")):
    host, _, port = hp.strip().partition(":")
    CACHE_NODES.append((f"cache-{i}", host, int(port or 6379)))

# Virtual nodes per cache node on the consistent-hash ring (higher = more even).
VIRTUAL_NODES = _int("VIRTUAL_NODES", 150)

# --- Meta Redis: trending counters + recent-activity window (not sharded) ---
META_HOST = os.getenv("META_HOST", "localhost")
META_PORT = _int("META_PORT", 6379)

# --- Suggestions ---
TOP_K = _int("TOP_K", 10)        # suggestions returned to the user
# Each Trie node caches a slightly larger candidate POOL, so the recency-aware
# ranking has more than TOP_K candidates to re-order.
POOL_K = _int("POOL_K", 30)

# --- Cache TTLs (cache expiry / invalidation) ---
# Basic ranking changes slowly -> longer TTL. Recency ranking changes fast -> short.
CACHE_TTL_BASIC = _int("CACHE_TTL_BASIC", 60)
CACHE_TTL_RECENCY = _int("CACHE_TTL_RECENCY", 5)

# --- Recency-aware ranking (the enhanced trending version) ---
# combined_score = all_time_count + RECENCY_ALPHA * recent_count_in_window
RECENCY_ALPHA = _int("RECENCY_ALPHA", 50)
RECENT_WINDOW_MINUTES = _int("RECENT_WINDOW_MINUTES", 10)
# "basic" (sort by overall count) is the default; "recency" enables the enhanced mode.
DEFAULT_RANKING = os.getenv("DEFAULT_RANKING", "basic")

# --- Batch writes ---
BATCH_MAX_SIZE = _int("BATCH_MAX_SIZE", 100)            # flush after this many distinct terms
BATCH_FLUSH_INTERVAL_SECONDS = _int("BATCH_FLUSH_INTERVAL_SECONDS", 5)  # ...or this often

# --- Dataset ---
# Where the (query,count) CSV lives, and how big a synthetic one to generate.
DATASET_PATH = os.getenv("DATASET_PATH", "data/queries.csv")
DATASET_SIZE = _int("DATASET_SIZE", 100000)
