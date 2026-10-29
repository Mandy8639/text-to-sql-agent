"""Foreign-key graph of the documented schema, used to add the tables a join needs.

If retrieval picks `order_payments` and `customers`, the query also needs `orders` to join
them. `connect()` adds the tables on the shortest FK paths between the picked tables, rather
than every neighbour, so the prompt stays small.
"""

from collections import deque
from itertools import combinations
from typing import Any


def fk_graph(doc: dict[str, Any], *, include_soft: bool = True) -> dict[str, set[str]]:
    """Undirected adjacency: table -> tables it joins to directly.

    Soft links (`enforced: false`, the zip-prefix lookups into geolocation) can be left out:
    they are lookups, not paths between business entities.
    """
    graph: dict[str, set[str]] = {t: set() for t in doc["tables"]}
    for table, spec in doc["tables"].items():
        for rel in spec["relationships"]:
            if not include_soft and rel.get("enforced") is False:
                continue
            other = rel["references"].split(".")[0]
            if other != table:
                graph[table].add(other)
                graph[other].add(table)
    return graph


def _shortest_path(graph: dict[str, set[str]], start: str, goal: str) -> list[str]:
    """BFS path from start to goal (inclusive), or [] if unreachable. Ties break by name."""
    prev: dict[str, str | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            path = []
            while node is not None:
                path.append(node)
                node = prev[node]
            return path[::-1]
        for nxt in sorted(graph[node]):
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    return []


def connect(doc: dict[str, Any], tables: list[str]) -> list[str]:
    """`tables` plus every table on a shortest join path between any two of them.

    Paths use enforced foreign keys first and fall back to soft links only when nothing else
    connects (e.g. geolocation). Keeps the input order, then appends the bridging tables
    (sorted) for a stable prompt.
    """
    hard, full = fk_graph(doc, include_soft=False), fk_graph(doc)
    picked = list(dict.fromkeys(tables))
    bridges: set[str] = set()
    for a, b in combinations(picked, 2):
        bridges.update(_shortest_path(hard, a, b) or _shortest_path(full, a, b))
    return picked + sorted(bridges - set(picked))
