"""
The database layer. Postgres is our durable source of truth: if the process
restarts, we rebuild the in-memory Trie and warm the cache from this table.

Data model
----------
One table is enough:

    search_queries(
        query            TEXT PRIMARY KEY,    -- the search string, e.g. "iphone 15"
        count            BIGINT NOT NULL,     -- how many times it has been searched
        last_searched_at TIMESTAMPTZ
    )

`query` is the primary key, which gives a unique index for free and lets us do an
"insert or add to the existing count" in a single statement (UPSERT). `count` is the
popularity the suggestions rank by.

The (blocking) psycopg2 calls are run from async code via `asyncio.to_thread`, so
they never block the event loop.
"""

from typing import Dict, List, Tuple

from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import execute_values

from . import config, metrics

_pool: SimpleConnectionPool | None = None


def init_pool() -> None:
    global _pool
    _pool = SimpleConnectionPool(
        minconn=1, maxconn=5,
        host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
        user=config.DB_USER, password=config.DB_PASSWORD,
    )


def close_pool() -> None:
    if _pool is not None:
        _pool.closeall()


def create_schema() -> None:
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS search_queries (
                    query            TEXT PRIMARY KEY,
                    count            BIGINT NOT NULL DEFAULT 0,
                    last_searched_at TIMESTAMPTZ
                );
                """
            )
        conn.commit()
    finally:
        _pool.putconn(conn)


def count_rows() -> int:
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM search_queries;")
            metrics.db_reads += 1
            return cur.fetchone()[0]
    finally:
        _pool.putconn(conn)


def load_all() -> List[Tuple[str, int]]:
    """Read every (query, count) so we can rebuild the Trie on startup."""
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT query, count FROM search_queries;")
            metrics.db_reads += 1
            return [(row[0], row[1]) for row in cur.fetchall()]
    finally:
        _pool.putconn(conn)


def ingest_csv(path: str) -> int:
    """Bulk-load a `query,count` CSV into the table. Used once to seed the dataset.

    We read the file, normalise queries to lowercase, aggregate duplicates, and write
    everything with a single multi-row INSERT (in chunks). Returns rows ingested.
    """
    import csv

    counts: Dict[str, int] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # skip the "query,count" header row
        for row in reader:
            if len(row) < 2:
                continue
            query = row[0].strip().lower()
            if not query:
                continue
            try:
                c = int(row[1])
            except ValueError:
                continue
            counts[query] = counts.get(query, 0) + c

    rows = list(counts.items())
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            # Insert in chunks so a single statement isn't gigantic.
            for start in range(0, len(rows), 5000):
                chunk = rows[start:start + 5000]
                execute_values(
                    cur,
                    "INSERT INTO search_queries (query, count) VALUES %s "
                    "ON CONFLICT (query) DO UPDATE SET count = EXCLUDED.count;",
                    chunk,
                )
        conn.commit()
        return len(rows)
    finally:
        _pool.putconn(conn)


def bulk_upsert(increments: Dict[str, int]) -> List[Tuple[str, int]]:
    """Apply a batch of count increments in ONE round trip; return the NEW totals.

    New queries are inserted; existing ones have the increment added to their count.
    `ON CONFLICT ... DO UPDATE` does both in a single statement, and `RETURNING` hands
    back the new totals so we can update the in-memory Trie to match. This is the core
    of the batch-write optimisation: one statement for the whole batch instead of one
    UPDATE per search event.
    """
    if not increments:
        return []

    rows = list(increments.items())
    conn = _pool.getconn()
    try:
        with conn.cursor() as cur:
            result = execute_values(
                cur,
                """
                INSERT INTO search_queries (query, count, last_searched_at)
                VALUES %s
                ON CONFLICT (query)
                DO UPDATE SET count            = search_queries.count + EXCLUDED.count,
                              last_searched_at = now()
                RETURNING query, count;
                """,
                rows,                          # each row is (query, increment)
                template="(%s, %s, now())",    # last_searched_at is set to now()
                fetch=True,
            )
        conn.commit()
        metrics.db_write_calls += 1
        metrics.db_rows_written += len(result)
        return [(r[0], r[1]) for r in result]
    finally:
        _pool.putconn(conn)
