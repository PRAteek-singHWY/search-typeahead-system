"""
Generate a synthetic search-query dataset of at least 100,000 unique queries with a
realistic popularity distribution, and write it as a `query,count` CSV.

Why synthetic?
--------------
The brief allows any open-source dataset with (query, count) entries, or deriving
counts by aggregation. A generated dataset is fully reproducible (fixed random seed),
needs no download, and lets us control the size and the popularity curve. Any real
CSV in the same `query,count` format can be dropped in instead — see db.ingest_csv.

How counts are assigned
-----------------------
Real search popularity is heavy-tailed: a few queries are hugely popular and most are
rare (a Zipf/Pareto-like curve). We draw counts from a Pareto distribution so the
data looks like real traffic, which makes the ranking and trending behaviour
meaningful.
"""

import csv
import os
import random

# Small themed vocabularies. Their combinations produce far more than 100k unique
# queries, and the variety gives many distinct prefixes for the typeahead to match.
HEADS = [
    "iphone", "samsung galaxy", "macbook", "laptop", "headphones", "monitor",
    "keyboard", "mouse", "smart watch", "tablet", "camera", "tv", "speaker",
    "router", "ssd", "graphics card", "processor", "power bank", "charger", "cable",
    "python", "java", "javascript", "react", "docker", "kubernetes", "sql",
    "system design", "data structures", "algorithms", "machine learning", "linux",
    "running shoes", "backpack", "office chair", "coffee maker", "air fryer",
    "vacuum cleaner", "water bottle", "sunglasses", "jacket", "sneakers", "watch",
    "how to learn", "how to make", "how to install", "how to fix", "best",
    "cheap", "top rated", "review of", "tutorial for", "guide to",
    "weather in", "flights to", "hotels in", "restaurants in", "things to do in",
    "news about", "stock price of", "recipe for", "movie", "song",
]

MODS = [
    "pro", "max", "plus", "mini", "lite", "ultra", "2026", "review", "price",
    "deals", "online", "near me", "for beginners", "tutorial", "guide", "tips",
    "vs android", "vs windows", "comparison", "specs", "features", "setup",
    "for students", "for gaming", "for work", "for travel", "under 500",
    "best brand", "wireless", "bluetooth", "usb c", "fast", "cheap", "premium",
    "black", "white", "blue", "with case", "accessories", "warranty",
]

# A larger tail vocabulary so HEADS x MODS x TAILS comfortably exceeds 100k unique
# queries. A couple of empty strings keep some queries to two words.
TAILS = [
    "", "", "review", "price", "near me", "2026", "2025", "deals", "guide", "online",
    "best", "cheap", "offers", "discount", "buy", "comparison", "alternatives",
    "for sale", "in india", "in usa", "uk", "amazon", "flipkart", "ebay",
    "specifications", "release date", "rumors", "leak", "vs competitors",
    "pros and cons", "ratings", "top picks", "explained", "step by step",
    "for beginners", "advanced", "free", "premium", "trial", "demo",
    "tips and tricks", "common mistakes", "checklist", "cheatsheet", "examples",
    "use cases", "benchmarks", "teardown", "unboxing", "hands on",
]

# Queries from the assignment's example table, with their given counts, so the demo
# lines up with the brief.
SEED_EXAMPLES = [
    ("iphone", 100000),
    ("iphone 15", 85000),
    ("iphone charger", 60000),
    ("java tutorial", 40000),
]


def generate(path: str, size: int, seed: int = 42) -> int:
    random.seed(seed)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    seen = set()
    rows = []
    for q, c in SEED_EXAMPLES:
        if q not in seen:
            seen.add(q)
            rows.append((q, c))

    # Build the combination space, then shuffle so counts aren't correlated with text.
    combos = []
    for head in HEADS:
        for mod in MODS:
            combos.append(f"{head} {mod}")
            for tail in TAILS:
                if tail:
                    combos.append(f"{head} {mod} {tail}")
    random.shuffle(combos)

    for q in combos:
        if len(rows) >= size:
            break
        if q in seen:
            continue
        seen.add(q)
        # Pareto draw -> heavy-tailed counts (a few large, many small).
        count = int(max(1, random.paretovariate(1.3) * 8))
        rows.append((q, count))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query", "count"])
        writer.writerows(rows)

    return len(rows)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "data/queries.csv"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100000
    total = generate(out, n)
    print(f"Wrote {total} queries to {out}")
