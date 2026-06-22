"""
The Trie (prefix tree) is the in-memory suggestion index.

Idea
----
A Trie stores words character by character along a path from the root. The word
"cat" creates the path  root -> c -> a -> t. The word "car" shares the "c -> a"
part and then branches to "r". So every node represents a *prefix*, and all the
words that start with that prefix live in the subtree below it.

The naive way to autocomplete a prefix is: walk to the prefix node, then explore the
whole subtree to collect every word, sort by popularity, and return the best K. That
subtree walk is expensive for a short, popular prefix like "i".

Our optimisation: **every node caches the top candidates of its own subtree**. A
query is then "walk to the prefix node and read its cached list" — O(length of the
prefix), independent of how many words match. This is the standard typeahead trick.

We cache a POOL of candidates (a bit more than the 10 we return) at each node, so the
recency-aware ranking (see suggester.py) has spare candidates to re-order.

Two build paths
---------------
1. `bulk_build` — used once at startup with the whole dataset. It inserts every word
   first, then computes each node's top pool in a single bottom-up (post-order) pass.
   That's far faster than maintaining the pool on every insert when loading 100k+
   rows.
2. `insert` — used for live updates from the batch writer. It maintains the pool
   incrementally along the path. Correct because counts only ever increase.
"""

from typing import Dict, Iterable, List, Optional, Tuple

Suggestion = Tuple[str, int]  # (term, count)


class TrieNode:
    # __slots__ avoids a per-node __dict__, saving memory across many nodes.
    __slots__ = ("children", "top", "is_word", "term", "count")

    def __init__(self) -> None:
        self.children: Dict[str, "TrieNode"] = {}
        self.top: List[Suggestion] = []  # cached top-pool for this subtree
        self.is_word = False             # a term ends exactly here
        self.term: Optional[str] = None  # the term text (only on word-end nodes)
        self.count = 0                   # that term's all-time search count


class Trie:
    def __init__(self, pool: int = 30) -> None:
        self.root = TrieNode()
        self.pool = pool     # how many candidates each node caches
        self._count = 0      # number of distinct terms (diagnostics)

    # ---------- build paths ----------
    def bulk_build(self, items: Iterable[Suggestion]) -> None:
        """Load the whole dataset, then compute every node's top pool in one pass."""
        for term, count in items:
            node = self.root
            for ch in term:
                node = node.children.setdefault(ch, TrieNode())
            if not node.is_word:
                self._count += 1
            node.is_word = True
            node.term = term
            node.count = count
        self._compute_top(self.root)

    def _compute_top(self, node: TrieNode) -> List[Suggestion]:
        """Post-order: a node's top pool = best of its own word + its children's pools."""
        merged: List[Suggestion] = []
        if node.is_word:
            merged.append((node.term, node.count))
        for child in node.children.values():
            merged.extend(self._compute_top(child))
        merged.sort(key=lambda pair: pair[1], reverse=True)
        node.top = merged[: self.pool]
        return node.top

    def insert(self, term: str, count: int) -> None:
        """Insert or update one term (live updates from the batch writer)."""
        node = self.root
        self._update_top(node, term, count)
        for ch in term:
            node = node.children.setdefault(ch, TrieNode())
            self._update_top(node, term, count)
        if not node.is_word:
            node.is_word = True
            self._count += 1
        node.term = term
        node.count = count

    def _update_top(self, node: TrieNode, term: str, count: int) -> None:
        """Keep node.top as the best `pool` candidates, deduplicated by term."""
        node.top = [(t, c) for (t, c) in node.top if t != term]
        node.top.append((term, count))
        node.top.sort(key=lambda pair: pair[1], reverse=True)
        del node.top[self.pool:]

    # ---------- queries ----------
    def _node_for(self, prefix: str) -> Optional[TrieNode]:
        node = self.root
        for ch in prefix:
            node = node.children.get(ch)
            if node is None:
                return None
        return node

    def candidates(self, prefix: str) -> List[Suggestion]:
        """The cached candidate pool for a prefix (best-first). Empty if no match."""
        node = self._node_for(prefix)
        return list(node.top) if node else []

    def get_count(self, term: str) -> int:
        """All-time count for an exact term (0 if unknown). Used by recency ranking."""
        node = self._node_for(term)
        return node.count if (node and node.is_word) else 0

    def size(self) -> int:
        return self._count
