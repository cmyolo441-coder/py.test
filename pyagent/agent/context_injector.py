"""Smart context injector — automatically add relevant context to conversations.

Inspired by gsd-pi's context injection, this module provides:
  - Automatic context detection based on task type
  - File context injection (read relevant files)
  - Project context injection (README, docs, etc.)
  - History context injection (relevant past decisions)

This helps the agent understand the project without explicit context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ContextSource:
    """A source of context information."""
    name: str
    content: str
    priority: int = 0  # Higher = more important
    source_type: str = "file"  # file | project | history | memory


class ContextInjector:
    """Inject relevant context into conversations."""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self._context_cache: dict[str, ContextSource] = {}

    def _find_project_root(self) -> Path | None:
        """Find the project root directory."""
        current = self.project_dir

        # Look for common project markers
        markers = [
            ".git",
            "pyproject.toml",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "Makefile",
            "README.md",
        ]

        for _ in range(10):  # Max 10 levels up
            for marker in markers:
                if (current / marker).exists():
                    return current

            parent = current.parent
            if parent == current:
                break
            current = parent

        return None

    def _read_file_safe(self, path: Path, max_lines: int = 100) -> str | None:
        """Read a file safely, truncating if needed."""
        try:
            if not path.exists() or not path.is_file():
                return None

            # Skip binary files and large files
            size = path.stat().st_size
            if size > 100_000:  # 100KB limit
                return None

            content = path.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")

            if len(lines) > max_lines:
                content = "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"

            return content
        except Exception:
            return None

    def detect_task_type(self, user_input: str) -> str:
        """Detect the type of task from user input.

        Returns:
            Task type: "code", "debug", "review", "docs", "test", "general"
        """
        text = user_input.lower()

        # Debug patterns
        if any(w in text for w in ["debug", "error", "fix", "bug", "issue", "problem"]):
            return "debug"

        # Review patterns
        if any(w in text for w in ["review", "audit", "check", "analyze", "evaluate"]):
            return "review"

        # Documentation patterns
        if any(w in text for w in ["doc", "readme", "guide", "explain", "document"]):
            return "docs"

        # Test patterns
        if any(w in text for w in ["test", "tests", "testing", "coverage", "spec"]):
            return "test"

        # Code patterns
        if any(w in text for w in ["build", "create", "implement", "write", "code", "function", "class"]):
            return "code"

        return "general"

    def get_project_context(self) -> list[ContextSource]:
        """Get project-level context (README, structure, etc.)."""
        sources: list[ContextSource] = []

        root = self._find_project_root()
        if root is None:
            return sources

        # README
        readme = root / "README.md"
        if readme.exists():
            content = self._read_file_safe(readme, max_lines=50)
            if content:
                sources.append(ContextSource(
                    name="README.md",
                    content=content,
                    priority=100,
                    source_type="project",
                ))

        # Project structure
        try:
            structure_lines: list[str] = []
            for item in sorted(root.iterdir()):
                if item.name.startswith(".") or item.name == "node_modules":
                    continue
                if item.is_dir():
                    structure_lines.append(f"  {item.name}/")
                else:
                    structure_lines.append(f"  {item.name}")

            if structure_lines:
                structure = "Project structure:\n" + "\n".join(structure_lines[:30])
                sources.append(ContextSource(
                    name="project_structure",
                    content=structure,
                    priority=90,
                    source_type="project",
                ))
        except Exception:
            pass

        # pyproject.toml or setup.py
        for config_name in ["pyproject.toml", "setup.py", "setup.cfg"]:
            config_path = root / config_name
            if config_path.exists():
                content = self._read_file_safe(config_path, max_lines=30)
                if content:
                    sources.append(ContextSource(
                        name=config_name,
                        content=content,
                        priority=80,
                        source_type="project",
                    ))
                    break

        return sources

    def get_file_context(self, file_path: str) -> list[ContextSource]:
        """Get context for a specific file."""
        sources: list[ContextSource] = []

        path = Path(file_path)
        if not path.exists():
            return sources

        # Read the file
        content = self._read_file_safe(path)
        if content:
            sources.append(ContextSource(
                name=path.name,
                content=content,
                priority=100,
                source_type="file",
            ))

        # Read related files (same directory, similar name)
        parent = path.parent
        stem = path.stem

        for related in parent.glob("*.py"):
            if related.name == path.name:
                continue
            if stem in related.stem or related.stem in stem:
                content = self._read_file_safe(related, max_lines=50)
                if content:
                    sources.append(ContextSource(
                        name=related.name,
                        content=content,
                        priority=70,
                        source_type="file",
                    ))

        return sources

    def get_debug_context(self, error_message: str) -> list[ContextSource]:
        """Get context relevant to debugging an error."""
        sources: list[ContextSource] = []

        # Try to extract file references from error
        file_pattern = r'File "([^"]+)"'
        files = re.findall(file_pattern, error_message)

        for file_path in files[:3]:  # Limit to 3 files
            path = Path(file_path)
            if path.exists():
                content = self._read_file_safe(path)
                if content:
                    sources.append(ContextSource(
                        name=path.name,
                        content=content,
                        priority=90,
                        source_type="file",
                    ))

        return sources

    def inject_context(
        self,
        user_input: str,
        messages: list[dict[str, Any]],
        max_context_tokens: int = 4000,
    ) -> list[dict[str, Any]]:
        """Inject relevant context into the conversation.

        Args:
            user_input: The user's input.
            messages: Current conversation messages.
            max_context_tokens: Maximum tokens for context (approximate).

        Returns:
            Updated messages with context injected.
        """
        task_type = self.detect_task_type(user_input)

        # Collect relevant context
        context_sources: list[ContextSource] = []

        # Always add project context
        context_sources.extend(self.get_project_context())

        # Add task-specific context
        if task_type == "debug":
            context_sources.extend(self.get_debug_context(user_input))
        elif task_type in ("code", "review", "test"):
            # Try to find relevant files mentioned in input
            file_pattern = r'[\w/]+\.py'
            files = re.findall(file_pattern, user_input)
            for f in files[:3]:
                context_sources.extend(self.get_file_context(f))

        # Sort by priority
        context_sources.sort(key=lambda s: s.priority, reverse=True)

        # Build context message
        context_parts: list[str] = []
        total_tokens = 0

        for source in context_sources:
            # Approximate tokens (1 token ≈ 4 chars)
            source_tokens = len(source.content) // 4

            if total_tokens + source_tokens > max_context_tokens:
                break

            context_parts.append(f"=== {source.name} ===\n{source.content}")
            total_tokens += source_tokens

        if not context_parts:
            return messages

        # Inject context as a system message after the first user message
        context_text = "\n\n".join(context_parts)
        context_message = {
            "role": "system",
            "content": f"Relevant context:\n\n{context_text}",
        }

        # Find the first user message and insert context after it
        new_messages = []
        injected = False

        for msg in messages:
            new_messages.append(msg)
            if not injected and msg.get("role") == "user":
                new_messages.append(context_message)
                injected = True

        # If no user message found, prepend context
        if not injected:
            new_messages = [context_message] + new_messages

        return new_messages


# Global instance
_injector: ContextInjector | None = None


def get_context_injector() -> ContextInjector:
    """Get the global context injector instance."""
    global _injector
    if _injector is None:
        _injector = ContextInjector()
    return _injector
