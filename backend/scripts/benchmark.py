"""
Measure suggestion-API latency (including p95) and read the server's cache hit rate
and database read/write counts. Produces the performance numbers the brief asks for
in sections 10 and 12.

Usage (from the backend/ directory, with the stack running):
    python -m scripts.benchmark               # 2000 requests against localhost:8000
    python -m scripts.benchmark 5000 http://localhost:8000

Only uses the standard library, so it runs anywhere.
"""

import json
import random
import sys
import time
import urllib.parse
import urllib.request

# A spread of prefixes a real user might type. Repeats are intentional: they let the
# cache warm up, which is exactly what we want to measure the hit rate for.
PREFIXES = [
    "ip", "iph", "iphone", "sam", "samsung", "mac", "macbook", "lap", "laptop",
    "head", "how", "how to", "how to learn", "best", "che", "py", "python", "java",
    "react", "doc", "docker", "sql", "sys", "system design", "data", "wea", "weather",
    "hot", "hotels", "run", "running", "back", "backpack", "cof", "coffee",
]


def _get(url: str) -> dict:
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read().decode())


def percentile(values, p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100) * (len(s) - 1))))
    return s[idx]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    base = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"

    random.seed(7)
    latencies_ms = []
    print(f"Firing {n} /suggest requests at {base} ...")
    for _ in range(n):
        prefix = random.choice(PREFIXES)
        url = f"{base}/suggest?q={urllib.parse.quote(prefix)}"
        t0 = time.perf_counter()
        _get(url)
        latencies_ms.append((time.perf_counter() - t0) * 1000)

    metrics = _get(f"{base}/metrics")

    print("\n--- Latency (ms) ---")
    print(f"  count : {len(latencies_ms)}")
    print(f"  avg   : {sum(latencies_ms) / len(latencies_ms):.2f}")
    print(f"  p50   : {percentile(latencies_ms, 50):.2f}")
    print(f"  p95   : {percentile(latencies_ms, 95):.2f}")
    print(f"  p99   : {percentile(latencies_ms, 99):.2f}")
    print(f"  max   : {max(latencies_ms):.2f}")

    print("\n--- Server metrics ---")
    print(f"  cache hit rate : {metrics['cache']['hit_rate'] * 100:.1f}% "
          f"({metrics['cache']['hits']} hits / {metrics['cache']['misses']} misses)")
    print(f"  db reads       : {metrics['database']['reads']}")
    print(f"  db write calls : {metrics['database']['write_calls']}")
    print(f"  db rows written: {metrics['database']['rows_written']}")


if __name__ == "__main__":
    main()
