"""
Tests for the pure in-memory logic (Trie + consistent hashing). These need no
database or Redis, so they run anywhere.

From the backend/ directory:
    python -m tests.test_core      # plain run, prints OK for each
    pytest tests/test_core.py      # if pytest is installed
"""

from app.consistent_hash import ConsistentHashRing
from app.trie import Trie


def test_trie_bulk_build_ranks_by_count():
    t = Trie(pool=30)
    t.bulk_build([("car", 5), ("cat", 9), ("cane", 2)])
    results = t.candidates("ca")
    assert [term for term, _ in results] == ["cat", "car", "cane"]


def test_trie_prefix_match_only():
    t = Trie(pool=30)
    t.bulk_build([("apple", 10), ("apply", 8), ("banana", 99)])
    # "banana" is more popular but must NOT appear for prefix "app".
    terms = [term for term, _ in t.candidates("app")]
    assert terms == ["apple", "apply"]


def test_trie_missing_prefix_is_empty():
    t = Trie(pool=30)
    t.bulk_build([("apple", 3)])
    assert t.candidates("xyz") == []


def test_trie_incremental_insert_updates_ranking():
    t = Trie(pool=30)
    t.bulk_build([("dog", 1), ("door", 10)])
    t.insert("dog", 50)  # dog overtakes door after a live update
    results = t.candidates("do")
    assert results[0][0] == "dog"
    assert [term for term, _ in results].count("dog") == 1  # no duplicate


def test_trie_get_count():
    t = Trie(pool=30)
    t.bulk_build([("python tutorial", 1500)])
    assert t.get_count("python tutorial") == 1500
    assert t.get_count("nonexistent") == 0


def test_pool_is_capped():
    t = Trie(pool=2)
    t.bulk_build([("aa", 1), ("ab", 2), ("ac", 3), ("ad", 4)])
    terms = [term for term, _ in t.candidates("a")]
    assert terms == ["ad", "ac"]  # only the best 2 kept


def test_consistent_hashing_is_balanced():
    ring = ConsistentHashRing(["cache-0", "cache-1", "cache-2"], virtual_nodes=150)
    counts = {f"cache-{i}": 0 for i in range(3)}
    for n in range(6000):
        counts[ring.get_node(f"prefix-{n}")] += 1
    # ~2000 each; allow a generous band.
    for node, c in counts.items():
        assert 1300 < c < 2700, f"{node} got {c} (too uneven)"


def test_consistent_hashing_minimal_remap_on_add():
    keys = [f"prefix-{n}" for n in range(4000)]
    ring = ConsistentHashRing(["cache-0", "cache-1", "cache-2"], virtual_nodes=150)
    before = {k: ring.get_node(k) for k in keys}
    ring.add_node("cache-3")
    after = {k: ring.get_node(k) for k in keys}
    moved = sum(1 for k in keys if before[k] != after[k])
    fraction = moved / len(keys)
    # Adding a 4th node should move roughly 1/4 of keys — far less than all of them.
    assert 0.12 < fraction < 0.40, f"remapped {fraction:.0%} of keys"


def _run_all():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\nAll {len(tests)} tests passed.")


if __name__ == "__main__":
    _run_all()
