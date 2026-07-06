"""Knowledge graph — models entities and relationships in a codebase.

Nodes: files, classes, functions, modules, imports
Edges: imports, defines, calls, inherits, depends-on

Built by parsing Python files with AST. Supports Cypher-like queries
(simplified) and visualisation as an adjacency list.
"""
from __future__ import annotations

import ast
import json
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class GraphNode:
    id: str  # unique identifier (e.g. "agent/app.py:App")
    kind: str  # "file" | "class" | "function" | "module" | "import"
    name: str
    location: str = ""  # file:line
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str  # node id
    target: str  # node id
    kind: str  # "imports" | "defines" | "calls" | "inherits" | "depends-on"
    metadata: dict[str, Any] = field(default_factory=dict)


class KnowledgeGraph:
    """A codebase knowledge graph."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)  # {source: [(target, kind)]}

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)
        self._adjacency[edge.source].append((edge.target, edge.kind))

    def get_node(self, node_id: str) -> GraphNode | None:
        return self.nodes.get(node_id)

    def neighbors(self, node_id: str, edge_kind: str | None = None) -> list[str]:
        results = []
        for target, kind in self._adjacency.get(node_id, []):
            if edge_kind is None or kind == edge_kind:
                results.append(target)
        return results

    def find(self, name: str, kind: str | None = None) -> list[GraphNode]:
        """Find nodes by name (substring match)."""
        results = []
        for node in self.nodes.values():
            if name.lower() in node.name.lower():
                if kind is None or node.kind == kind:
                    results.append(node)
        return results

    def shortest_path(self, source: str, target: str) -> list[str] | None:
        """BFS shortest path between two nodes (ignoring edge kind)."""
        if source not in self.nodes or target not in self.nodes:
            return None
        visited = {source}
        queue = [[source]]
        while queue:
            path = queue.pop(0)
            node = path[-1]
            if node == target:
                return path
            for neighbor, _ in self._adjacency.get(node, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    def top_entities(self, n: int = 10) -> list[tuple[GraphNode, int]]:
        """Return the most-connected nodes (hubs)."""
        connections = {node_id: len(self.neighbors(node_id)) for node_id in self.nodes}
        sorted_nodes = sorted(connections.items(), key=lambda x: -x[1])
        return [(self.nodes[nid], count) for nid, count in sorted_nodes[:n]]

    def stats(self) -> dict[str, Any]:
        kind_counts: dict[str, int] = defaultdict(int)
        for node in self.nodes.values():
            kind_counts[node.kind] += 1
        edge_counts: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            edge_counts[edge.kind] += 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_kinds": dict(kind_counts),
            "edge_kinds": dict(edge_counts),
        }

    def dashboard(self) -> str:
        s = self.stats()
        lines = [
            "Knowledge graph:",
            f"  nodes: {s['nodes']}",
            f"  edges: {s['edges']}",
            "  node kinds:",
        ]
        for k, v in s["node_kinds"].items():
            lines.append(f"    {k:<12} {v}")
        lines.append("  edge kinds:")
        for k, v in s["edge_kinds"].items():
            lines.append(f"    {k:<12} {v}")
        lines.append("\n  Top hubs:")
        for node, count in self.top_entities(5):
            lines.append(f"    {node.name:<30} {count} connections")
        return "\n".join(lines)

    def save(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges],
        }
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self, path: Path | str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.nodes = {n["id"]: GraphNode(**n) for n in data.get("nodes", [])}
            self.edges = [GraphEdge(**e) for e in data.get("edges", [])]
            self._adjacency = defaultdict(list)
            for edge in self.edges:
                self._adjacency[edge.source].append((edge.target, edge.kind))
            return True
        except (json.JSONDecodeError, OSError, TypeError):
            return False


def build_graph_from_codebase(root: Path | str, exclude_dirs: set[str] | None = None) -> KnowledgeGraph:
    """Parse a Python codebase and build a knowledge graph."""
    skip = exclude_dirs or {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", "dist", "build"}
    root = Path(root)
    kg = KnowledgeGraph()

    for py_file in root.rglob("*.py"):
        if any(part in skip for part in py_file.parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        file_id = str(py_file.relative_to(root))
        kg.add_node(GraphNode(
            id=f"file:{file_id}",
            kind="file",
            name=py_file.name,
            location=str(py_file),
            metadata={"path": str(py_file)},
        ))

        # Walk imports.
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_id = f"module:{alias.name}"
                    if import_id not in kg.nodes:
                        kg.add_node(GraphNode(id=import_id, kind="module", name=alias.name))
                    kg.add_edge(GraphEdge(source=f"file:{file_id}", target=import_id, kind="imports"))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                import_id = f"module:{module}"
                if import_id not in kg.nodes:
                    kg.add_node(GraphNode(id=import_id, kind="module", name=module))
                kg.add_edge(GraphEdge(source=f"file:{file_id}", target=import_id, kind="imports"))
            elif isinstance(node, ast.ClassDef):
                cls_id = f"{file_id}:{node.name}"
                kg.add_node(GraphNode(
                    id=cls_id, kind="class", name=node.name,
                    location=f"{py_file}:{node.lineno}",
                ))
                kg.add_edge(GraphEdge(source=f"file:{file_id}", target=cls_id, kind="defines"))
                # Inheritance edges.
                for base in node.bases:
                    base_name = ast.unparse(base) if hasattr(ast, "unparse") else ""
                    if base_name:
                        base_id = f"class:{base_name}"
                        if base_id not in kg.nodes:
                            kg.add_node(GraphNode(id=base_id, kind="class", name=base_name))
                        kg.add_edge(GraphEdge(source=cls_id, target=base_id, kind="inherits"))
                # Methods.
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_id = f"{cls_id}.{item.name}"
                        kg.add_node(GraphNode(
                            id=method_id, kind="function", name=item.name,
                            location=f"{py_file}:{item.lineno}",
                            metadata={"class": node.name},
                        ))
                        kg.add_edge(GraphEdge(source=cls_id, target=method_id, kind="defines"))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions only (methods handled above).
                # Skip if already added as a method.
                fn_id = f"{file_id}:{node.name}"
                if fn_id not in kg.nodes:
                    kg.add_node(GraphNode(
                        id=fn_id, kind="function", name=node.name,
                        location=f"{py_file}:{node.lineno}",
                    ))
                    kg.add_edge(GraphEdge(source=f"file:{file_id}", target=fn_id, kind="defines"))

    return kg


_kg: KnowledgeGraph | None = None


def get_knowledge_graph() -> KnowledgeGraph:
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph()
    return _kg
