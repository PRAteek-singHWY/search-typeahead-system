"""
Demonstrate consistent-hashing behaviour with plain logs (asked for in section 10).

It does two things:
  1. Shows how a batch of prefixes is distributed across the cache nodes (should be
     fairly even, thanks to virtual nodes).
  2. Adds a new cache node and reports what fraction of prefixes had to move. With
     consistent hashing this is roughly 1/(N+1) — only the keys in the new node's arc
     — instead of nearly everything (which is what `hash % N` would cause).

Run from the backend/ directory:
    python -m scripts.ch_demo
"""

from collections import Counter

from app.consistent_hash import ConsistentHashRing


def _sample_prefixes(n: int):
    # Deterministic synthetic prefixes p0, p1, ... — enough to see the distribution.
    return [f"prefix-{i}" for i in range(n)]


def main():
    prefixes = _sample_prefixes(10000)

    nodes_before = ["cache-0", "cache-1", "cache-2"]
    ring = ConsistentHashRing(nodes_before, virtual_nodes=150)

    print(f"Distribution across {len(nodes_before)} nodes (150 virtual nodes each):")
    placement_before = {p: ring.get_node(p) for p in prefixes}
    for node, c in sorted(Counter(placement_before.values()).items()):
        print(f"  {node}: {c} prefixes ({c / len(prefixes) * 100:.1f}%)")

    # Add a 4th node and see how many prefixes move.
    ring.add_node("cache-3")
    placement_after = {p: ring.get_node(p) for p in prefixes}

    moved = sum(1 for p in prefixes if placement_before[p] != placement_after[p])
    print(f"\nAfter adding cache-3:")
    for node, c in sorted(Counter(placement_after.values()).items()):
        print(f"  {node}: {c} prefixes ({c / len(prefixes) * 100:.1f}%)")
    print(f"\nPrefixes remapped: {moved} / {len(prefixes)} "
          f"({moved / len(prefixes) * 100:.1f}%)")
    print("With plain `hash % N` this would be close to 100%. Consistent hashing keeps "
          "it near 1/N, so most cached entries stay where they are.")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")  # so `app` is importable when run from backend/
    main()
