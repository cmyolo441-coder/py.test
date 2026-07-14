"""Zenith graph algorithms for local code intelligence."""
from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

@dataclass
class DirectedGraph:
    edges: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    def add(self, src: str, dst: str) -> None:
        self.edges[src].add(dst); self.edges.setdefault(dst, set())
    def nodes(self) -> set[str]:
        return set(self.edges) | {d for ds in self.edges.values() for d in ds}

def build_graph(edge_pairs: list[tuple[str, str]]) -> DirectedGraph:
    g = DirectedGraph()
    for a, b in edge_pairs:
        if a and b and a != b: g.add(a, b)
    return g

def graph_stats(g: DirectedGraph) -> dict[str, Any]:
    nodes = g.nodes(); deg = {n: len(g.edges.get(n, set())) for n in nodes}
    incoming = defaultdict(int)
    for a, ds in g.edges.items():
        for d in ds: incoming[d] += 1
    return {"nodes": len(nodes), "edges": sum(len(v) for v in g.edges.values()), "top_out": sorted(deg.items(), key=lambda x: -x[1])[:15], "top_in": sorted(incoming.items(), key=lambda x: -x[1])[:15]}

def shortest_path(g: DirectedGraph, start: str, target: str) -> list[str]:
    q = deque([(start, [start])]); seen = {start}
    while q:
        cur, path = q.popleft()
        if cur == target: return path
        for nxt in sorted(g.edges.get(cur, set())):
            if nxt not in seen:
                seen.add(nxt); q.append((nxt, path + [nxt]))
    return []

def transitive_closure(g: DirectedGraph, start: str, limit: int = 200) -> list[str]:
    out, seen, q = [], {start}, deque([start])
    while q and len(out) < limit:
        cur = q.popleft()
        for nxt in sorted(g.edges.get(cur, set())):
            if nxt not in seen:
                seen.add(nxt); out.append(nxt); q.append(nxt)
    return out

def strongly_connected(g: DirectedGraph) -> list[list[str]]:
    index = 0; stack=[]; on=set(); idx={}; low={}; comps=[]
    def visit(v: str) -> None:
        nonlocal index
        idx[v]=low[v]=index; index+=1; stack.append(v); on.add(v)
        for w in g.edges.get(v,set()):
            if w not in idx: visit(w); low[v]=min(low[v], low[w])
            elif w in on: low[v]=min(low[v], idx[w])
        if low[v] == idx[v]:
            comp=[]
            while True:
                w=stack.pop(); on.remove(w); comp.append(w)
                if w==v: break
            if len(comp)>1: comps.append(sorted(comp))
    for n in sorted(g.nodes()):
        if n not in idx: visit(n)
    return comps

def centrality(g: DirectedGraph) -> dict[str, float]:
    incoming=defaultdict(int); nodes=g.nodes()
    for a, ds in g.edges.items():
        for d in ds: incoming[d]+=1
    return {n: float(len(g.edges.get(n,set())) + incoming[n]*2) for n in nodes}

def dependency_layers(g: DirectedGraph) -> list[list[str]]:
    indeg={n:0 for n in g.nodes()}
    for ds in g.edges.values():
        for d in ds: indeg[d]+=1
    cur=sorted([n for n,d in indeg.items() if d==0]); layers=[]; used=set()
    while cur:
        layers.append(cur); nxt=[]
        for n in cur:
            used.add(n)
            for d in g.edges.get(n,set()):
                indeg[d]-=1
                if indeg[d]==0: nxt.append(d)
        cur=sorted(nxt)
    rest=sorted(set(indeg)-used)
    if rest: layers.append(rest)
    return layers
