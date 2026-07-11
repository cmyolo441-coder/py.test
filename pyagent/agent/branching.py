"""Session branching — navigate conversation history as a tree.

Inspired by gsd-pi's session manager, this module provides:
  - Branch conversations at any point
  - Navigate between branches
  - View conversation tree structure
  - Merge branches

Sessions are stored as append-only trees with parent-child relationships.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BranchNode:
    """A node in the conversation tree."""
    id: str
    parent_id: str | None
    message: dict[str, Any]
    timestamp: float
    children: list[str] = field(default_factory=list)
    label: str = ""
    name: str = ""  # Alias for label
    
    def __post_init__(self):
        if not self.name and self.label:
            self.name = self.label
        elif not self.label and self.name:
            self.label = self.name
    
    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0
    
    @property
    def depth(self) -> int:
        return 0  # Computed by tree


class ConversationTree:
    """A tree of conversation messages supporting branching."""
    
    def __init__(self):
        self.nodes: dict[str, BranchNode] = {}
        self.root_id: str | None = None
        self.leaf_id: str | None = None
    
    def add_message(
        self,
        message: dict[str, Any],
        parent_id: str | None = None,
        label: str = "",
    ) -> str:
        """Add a message to the tree.
        
        Args:
            message: The message dict (role, content, etc.).
            parent_id: Parent node ID (None for root).
            label: Optional label for this branch.
        
        Returns:
            The new node ID.
        """
        node_id = uuid.uuid4().hex[:12]
        
        # If no parent specified, use current leaf
        if parent_id is None:
            parent_id = self.leaf_id
        
        node = BranchNode(
            id=node_id,
            parent_id=parent_id,
            message=message,
            timestamp=time.time(),
            label=label,
        )
        
        self.nodes[node_id] = node
        
        # Update parent's children
        if parent_id and parent_id in self.nodes:
            self.nodes[parent_id].children.append(node_id)
        
        # Update root if first node
        if self.root_id is None:
            self.root_id = node_id
        
        # Update leaf
        self.leaf_id = node_id
        
        return node_id
    
    def get_path(self, node_id: str | None = None) -> list[BranchNode]:
        """Get the path from root to the specified node.
        
        Args:
            node_id: Target node ID (defaults to current leaf).
        
        Returns:
            List of nodes from root to target.
        """
        if node_id is None:
            node_id = self.leaf_id
        
        if node_id is None:
            return []
        
        path = []
        current = node_id
        
        while current is not None:
            node = self.nodes.get(current)
            if node is None:
                break
            path.append(node)
            current = node.parent_id
        
        path.reverse()
        return path
    
    def get_messages(self, node_id: str | None = None) -> list[dict[str, Any]]:
        """Get messages from root to the specified node.
        
        Args:
            node_id: Target node ID (defaults to current leaf).
        
        Returns:
            List of message dicts.
        """
        path = self.get_path(node_id)
        return [node.message for node in path]
    
    def branch(self, from_node_id: str | None = None) -> str:
        """Create a new branch from the specified node.
        
        Args:
            from_node_id: Node to branch from (defaults to current leaf).
        
        Returns:
            The new leaf node ID.
        """
        if from_node_id is None:
            from_node_id = self.leaf_id
        
        if from_node_id is None:
            raise ValueError("No node to branch from")
        
        # The new leaf is the same as the old leaf, but new messages
        # will be added as children of this node
        self.leaf_id = from_node_id
        return from_node_id
    
    def switch_branch(self, node_id: str) -> None:
        """Switch to a different branch.
        
        Args:
            node_id: The node to switch to.
        
        Raises:
            ValueError: If node doesn't exist.
        """
        if node_id not in self.nodes:
            raise ValueError(f"Node {node_id} not found")
        
        self.leaf_id = node_id
    
    def get_branches(self, node_id: str | None = None) -> list[BranchNode]:
        """Get all branches (nodes with multiple children).
        
        Args:
            node_id: Root node to search from (defaults to tree root).
        
        Returns:
            List of branch nodes.
        """
        if node_id is None:
            node_id = self.root_id
        
        if node_id is None:
            return []
        
        branches = []
        node = self.nodes.get(node_id)
        
        if node and len(node.children) > 1:
            branches.append(node)
        
        # Recurse into children
        if node:
            for child_id in node.children:
                branches.extend(self.get_branches(child_id))
        
        return branches
    
    def get_siblings(self, node_id: str) -> list[BranchNode]:
        """Get sibling nodes (nodes with the same parent).
        
        Args:
            node_id: The node to find siblings for.
        
        Returns:
            List of sibling nodes.
        """
        node = self.nodes.get(node_id)
        if node is None or node.parent_id is None:
            return []
        
        parent = self.nodes.get(node.parent_id)
        if parent is None:
            return []
        
        return [
            self.nodes[child_id]
            for child_id in parent.children
            if child_id != node_id and child_id in self.nodes
        ]
    
    def label_node(self, node_id: str, label: str) -> None:
        """Add or update a label on a node.
        
        Args:
            node_id: The node to label.
            label: The label text.
        """
        node = self.nodes.get(node_id)
        if node:
            node.label = label
    
    def delete_branch(self, node_id: str) -> bool:
        """Delete a branch and all its descendants.
        
        Args:
            node_id: The root of the branch to delete.
        
        Returns:
            True if deleted, False if not found.
        """
        if node_id not in self.nodes:
            return False
        
        node = self.nodes[node_id]
        
        # Can't delete root
        if node_id == self.root_id:
            return False
        
        # Remove from parent's children
        if node.parent_id and node.parent_id in self.nodes:
            parent = self.nodes[node.parent_id]
            parent.children = [c for c in parent.children if c != node_id]
        
        # Recursively delete descendants
        def delete_descendants(nid: str) -> None:
            n = self.nodes.get(nid)
            if n:
                for child_id in n.children:
                    delete_descendants(child_id)
                del self.nodes[nid]
        
        delete_descendants(node_id)
        
        # Update leaf if needed
        if self.leaf_id == node_id:
            self.leaf_id = node.parent_id
        
        return True
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize the tree to a dict."""
        return {
            "root_id": self.root_id,
            "leaf_id": self.leaf_id,
            "nodes": {
                nid: asdict(node)
                for nid, node in self.nodes.items()
            },
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationTree:
        """Deserialize a tree from a dict."""
        tree = cls()
        tree.root_id = data.get("root_id")
        tree.leaf_id = data.get("leaf_id")
        
        for nid, node_data in data.get("nodes", {}).items():
            tree.nodes[nid] = BranchNode(**node_data)
        
        return tree
    
    def save(self, path: Path) -> None:
        """Save the tree to a JSON file."""
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
    
    @classmethod
    def load(cls, path: Path) -> ConversationTree:
        """Load a tree from a JSON file."""
        if not path.exists():
            return cls()
        
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)
    
    def render_tree(self, node_id: str | None = None, depth: int = 0, max_depth: int = 10) -> str:
        """Render the tree as a string.
        
        Args:
            node_id: Node to render from (defaults to root).
            depth: Current depth.
            max_depth: Maximum depth to render.
        
        Returns:
            String representation of the tree.
        """
        if node_id is None:
            node_id = self.root_id
        
        if node_id is None:
            return "(empty tree)"
        
        if depth > max_depth:
            return "  " * depth + "..."
        
        node = self.nodes.get(node_id)
        if node is None:
            return ""
        
        lines = []
        indent = "  " * depth
        
        # Node info
        role = node.message.get("role", "?")
        content = node.message.get("content", "")
        if isinstance(content, str):
            preview = content[:50] + ("..." if len(content) > 50 else "")
        else:
            preview = str(content)[:50]
        
        marker = "●" if node_id == self.leaf_id else "○"
        label = f" [{node.label}]" if node.label else ""
        lines.append(f"{indent}{marker} {node_id} ({role}): {preview}{label}")
        
        # Children
        for child_id in node.children:
            lines.append(self.render_tree(child_id, depth + 1, max_depth))
        
        return "\n".join(lines)


# Global instance
_tree: ConversationTree | None = None


def get_conversation_tree() -> ConversationTree:
    """Get the global conversation tree instance."""
    global _tree
    if _tree is None:
        _tree = ConversationTree()
    return _tree


# Alias for backward compatibility with tests
class BranchManager:
    """Manage conversation branches (legacy API)."""
    
    def __init__(self, messages: list[dict[str, Any]] | None = None):
        self.tree = ConversationTree()
        self.branches: dict[str, BranchNode] = {}
        self.active_id = "main"
        self.messages: list[dict[str, Any]] = messages or []
        
        # Add initial messages to the main branch
        for msg in self.messages:
            self.tree.add_message(msg, label="main")
    
    def fork(self, name: str = "") -> BranchNode:
        """Fork a new branch from the current position."""
        node_id = self.tree.branch(self.tree.leaf_id)
        self.tree.label_node(node_id, name)
        self.active_id = name or node_id
        return self.tree.nodes[node_id]
    
    def switch(self, branch_id: str) -> None:
        """Switch to a different branch."""
        # Find node by label
        for nid, node in self.tree.nodes.items():
            if node.label == branch_id or nid == branch_id:
                self.tree.switch_branch(nid)
                self.active_id = branch_id
                return
        raise ValueError(f"Branch '{branch_id}' not found")
    
    def list_branches(self) -> list[BranchNode]:
        """List all branches."""
        return self.tree.get_branches()
    
    def add_message(self, message: dict[str, Any]) -> str:
        """Add a message to the current branch."""
        self.messages.append(message)
        return self.tree.add_message(message)
