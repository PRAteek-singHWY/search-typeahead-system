"""
Tiny in-process counters so we can report the numbers the assignment asks for in
section 10: cache hit rate, database read/write counts, and how much the batch
writer reduces writes.

These are plain integers updated from the (single-threaded) async event loop, so we
don't need locks. They reset when the process restarts — that's fine, they're for
the demo's performance report, not durable accounting.
"""

# Cache
cache_hits = 0
cache_misses = 0

# Database
db_reads = 0          # number of read queries issued to Postgres
db_write_calls = 0    # number of bulk-UPSERT statements (one per flush)
db_rows_written = 0   # total rows affected by those UPSERTs

# Writes / batching
search_events = 0     # how many /search submissions we received
batch_flushes = 0     # how many times the buffer was flushed


def snapshot() -> dict:
    total = cache_hits + cache_misses
    hit_rate = (cache_hits / total) if total else 0.0
    return {
        "cache": {
            "hits": cache_hits,
            "misses": cache_misses,
            "hit_rate": round(hit_rate, 4),
        },
        "database": {
            "reads": db_reads,
            "write_calls": db_write_calls,      # = number of batch flushes that wrote
            "rows_written": db_rows_written,
        },
        "writes": {
            "search_events_received": search_events,
            "batch_flushes": batch_flushes,
            # If we wrote per-event we'd have done `search_events` writes. We actually
            # did `db_write_calls`. This ratio is the write-reduction the batching buys.
            "write_reduction_factor": round(search_events / db_write_calls, 2)
            if db_write_calls else None,
        },
    }
