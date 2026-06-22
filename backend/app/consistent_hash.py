"""
Consistent hashing decides which cache node owns a given prefix key.

Why not just `hash(prefix) % number_of_nodes`?
----------------------------------------------
Plain modulo works until the number of nodes changes. If you go from 3 nodes to 4,
then `hash(p) % 3` and `hash(p) % 4` disagree for almost *every* prefix, so nearly
all cached entries land on a different node and the whole cache is effectively cold.
That is a disaster at scale.

Consistent hashing fixes this. We place the cache nodes on an imaginary circle (the
"ring"), and we also place each *prefix* on the same circle using the same hash
function. A prefix is owned by the first node you meet walking clockwise from the
prefix's position. When you add or remove one node, only the prefixes sitting in
that node's arc of the circle move — on average about 1/N of the keys, not all.

Virtual nodes
-------------
If each node sat at a single point, the arcs would be very uneven and one node could
get far more keys than another. So we place each node at MANY points ("virtual
nodes") spread around the ring. More points -> smoother, more even distribution.
"""

import bisect
import hashlib
from typing import List


class ConsistentHashRing:
    def __init__(self, nodes: List[str], virtual_nodes: int = 150) -> None:
        self.virtual_nodes = virtual_nodes
        self._ring = {}                # ring position (int) -> node id
        self._sorted_positions = []    # sorted ring positions, for fast lookup
        for node in nodes:
            self.add_node(node)

    def _hash(self, key: str) -> int:
        """Map any string to a point on the ring as a 32-bit integer.

        MD5 is used purely because it spreads values out evenly and is built in.
        It is not used for security, only for distribution.
        """
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()
        return int(digest, 16) % (2 ** 32)

    def add_node(self, node: str) -> None:
        """Place a node on the ring at `virtual_nodes` different positions."""
        for i in range(self.virtual_nodes):
            position = self._hash(f"{node}#{i}")
            self._ring[position] = node
            bisect.insort(self._sorted_positions, position)

    def remove_node(self, node: str) -> None:
        """Remove every virtual node belonging to a node (e.g. it went offline)."""
        for i in range(self.virtual_nodes):
            position = self._hash(f"{node}#{i}")
            if position in self._ring:
                del self._ring[position]
                idx = bisect.bisect_left(self._sorted_positions, position)
                if idx < len(self._sorted_positions) and self._sorted_positions[idx] == position:
                    self._sorted_positions.pop(idx)

    def get_node(self, key: str) -> str:
        """Return the node that owns `key`.

        Walk clockwise from the key's position to the first virtual node. Because the
        ring is circular, if we fall off the end we wrap back to the start.
        """
        if not self._sorted_positions:
            raise RuntimeError("Hash ring is empty — no nodes registered.")
        position = self._hash(key)
        # bisect_right finds the first ring position strictly greater than ours.
        idx = bisect.bisect_right(self._sorted_positions, position)
        if idx == len(self._sorted_positions):
            idx = 0  # wrap around the circle
        return self._ring[self._sorted_positions[idx]]
