"""Domain-specific exception hierarchy.

A clear exception tree makes error handling predictable across the codebase
and lets callers catch broad or narrow categories as needed.
"""
from __future__ import annotations


class AgentError(Exception):
    """Base class for all application errors."""


class ConfigError(AgentError):
    """Invalid or missing configuration."""


class ProviderError(AgentError):
    """A model provider failed."""


class ToolError(AgentError):
    """A tool failed to execute."""


class RateLimitError(AgentError):
    """Local rate limit exceeded."""


class AuthError(AgentError):
    """Authentication or authorization failure."""


class StorageError(AgentError):
    """Persistence layer failure."""
