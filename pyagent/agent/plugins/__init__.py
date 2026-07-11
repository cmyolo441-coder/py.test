"""Plugin subpackage: load user-defined tools at runtime."""

from __future__ import annotations

from .loader import load_plugins

__all__ = ["load_plugins"]
