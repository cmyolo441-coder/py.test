"""Terminal AI Agent — a clean, minimal terminal coding assistant."""

__version__ = "2.0.0"

from .config import Config
from .core import Agent
from .memory import Conversation
from .tools import ToolRegistry

__all__ = ["__version__", "Agent", "Config", "Conversation", "ToolRegistry"]
