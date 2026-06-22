"""
The suggester turns a prefix into a ranked list of suggestions. It supports the two
ranking modes the assignment describes.

Basic ranking (the 60% version)
--------------------------------
Sort the prefix's matching queries by **all-time count**, descending. The Trie
already keeps each prefix node's candidate pool sorted this way, so basic ranking is
just "take the top K of the pool".

Recency-aware ranking (the enhanced +20% version)
--------------------------------------------------
Recently searched queries should get a boost so fresh interest surfaces. We combine
the two signals with a simple, explainable formula:

    combined_score = all_time_count + ALPHA * recent_count_in_window

- *How recent searches are tracked:* the Activity module's sliding minute-window
  (see activity.py).
- *How recent activity affects ranking:* the `ALPHA * recent_count` term lifts queries
  that are being searched right now.
- *Avoiding permanent over-ranking:* `recent_count` comes from a sliding window, so a
  brief spike's boost disappears after the window passes — it can't dominate forever.
- *Surfacing brand-new queries:* the candidate set is the Trie pool **plus** any
  recently-hot queries matching the prefix, so a surging query can appear even before
  its all-time count climbs into the pool.

We display each suggestion's all-time `count`, but order by the mode's score — so in
recency mode you can literally see a lower-count-but-surging query sit above a
higher-count-but-quiet one.
"""

from typing import List, Tuple

from .activity import Activity
from .trie import Suggestion, Trie


class Suggester:
    def __init__(self, trie: Trie, activity: Activity, alpha: int) -> None:
        self.trie = trie
        self.activity = activity
        self.alpha = alpha

    async def suggest(self, prefix: str, mode: str, k: int) -> List[Suggestion]:
        candidates = self.trie.candidates(prefix)  # [(term, all_time_count)], best-first

        if mode != "recency":
            # Basic: the pool is already sorted by all-time count.
            return candidates[:k]

        # Recency mode: build the candidate set = Trie pool + recently-hot prefix matches.
        all_time = {term: count for term, count in candidates}
        for term, _recent in await self.activity.recent_matching_prefix(prefix):
            if term not in all_time:
                all_time[term] = self.trie.get_count(term)  # 0 if brand-new

        terms = list(all_time.keys())
        recent = await self.activity.recent_counts(terms)

        # Score and sort by the combined formula; display the all-time count.
        ranked: List[Tuple[str, int, int]] = []
        for term in terms:
            combined = all_time[term] + self.alpha * recent.get(term, 0)
            ranked.append((term, all_time[term], combined))
        ranked.sort(key=lambda r: r[2], reverse=True)

        return [(term, count) for term, count, _score in ranked[:k]]
