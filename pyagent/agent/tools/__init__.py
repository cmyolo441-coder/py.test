"""Tool registry and built-in tools for the terminal AI agent."""

from __future__ import annotations

from .base import Tool, ToolResult
from .registry import ToolRegistry, build_default_registry

__all__ = ["Tool", "ToolResult", "ToolRegistry", "build_default_registry"]
