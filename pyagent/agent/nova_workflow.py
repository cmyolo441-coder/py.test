"""Nova workflow DAG planner."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowNode:
    id: str
    title: str
    command: str = ""
    depends_on: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowDAG:
    name: str
    nodes: list[WorkflowNode]

    def order(self) -> list[WorkflowNode]:
        return topological_order(self.nodes)

    def stats(self) -> dict[str, Any]:
        return {"name": self.name, "nodes": len(self.nodes), "commands": sum(1 for n in self.nodes if n.command), "valid": validate_dag(self.nodes)[0]}


def topological_order(nodes: list[WorkflowNode]) -> list[WorkflowNode]:
    by_id = {n.id: n for n in nodes}
    indeg = {n.id: 0 for n in nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for dep in node.depends_on:
            if dep in by_id:
                indeg[node.id] += 1
                children[dep].append(node.id)
    q = deque([nid for nid, d in indeg.items() if d == 0])
    out: list[WorkflowNode] = []
    while q:
        nid = q.popleft()
        out.append(by_id[nid])
        for child in children[nid]:
            indeg[child] -= 1
            if indeg[child] == 0:
                q.append(child)
    return out if len(out) == len(nodes) else nodes


def validate_dag(nodes: list[WorkflowNode]) -> tuple[bool, str]:
    ordered = topological_order(nodes)
    if len(ordered) != len(nodes):
        return False, "cycle detected"
    ids = {n.id for n in nodes}
    for node in nodes:
        missing = [dep for dep in node.depends_on if dep not in ids]
        if missing:
            return False, f"missing dependency for {node.id}: {missing}"
    return True, "ok"


def build_workflow(task: str, commands: list[str], hot_files: list[str] | None = None) -> WorkflowDAG:
    hot_files = hot_files or []
    nodes = [
        WorkflowNode("W1", "Read indexed context", metadata={"hot_files": hot_files[:8]}),
        WorkflowNode("W2", "Plan smallest safe change", depends_on=["W1"]),
        WorkflowNode("W3", "Apply patch", depends_on=["W2"]),
        WorkflowNode("W4", "Review affected files", depends_on=["W3"], metadata={"files": hot_files[:12]}),
    ]
    prev = "W4"
    for idx, command in enumerate(commands[:6], 1):
        nid = f"V{idx}"
        nodes.append(WorkflowNode(nid, f"Verify: {command}", command=command, depends_on=[prev]))
        prev = nid
    return WorkflowDAG(name=task[:60] or "workflow", nodes=nodes)


def workflow_context(dag: WorkflowDAG) -> str:
    lines = [f"workflow: {dag.name}"]
    for node in dag.order():
        dep = f" after {', '.join(node.depends_on)}" if node.depends_on else ""
        cmd = f" -> `{node.command}`" if node.command else ""
        lines.append(f"- {node.id}: {node.title}{dep}{cmd}")
    return "\n".join(lines)


def merge_workflows(name: str, workflows: list[WorkflowDAG]) -> WorkflowDAG:
    nodes: list[WorkflowNode] = []
    for wi, wf in enumerate(workflows, 1):
        for node in wf.nodes:
            nodes.append(WorkflowNode(f"{wi}-{node.id}", node.title, node.command, [f"{wi}-{d}" for d in node.depends_on], node.metadata))
    return WorkflowDAG(name=name, nodes=nodes)
