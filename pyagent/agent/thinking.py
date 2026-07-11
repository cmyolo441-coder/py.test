"""Thinking levels — control agent reasoning depth.

Inspired by gsd-pi's thinking level support, this module provides:
  - Multiple thinking levels (off, minimal, low, medium, high)
  - Adjust reasoning depth based on task complexity
  - Token budget management
  - Level-specific system prompts
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ThinkingLevel(Enum):
    """Thinking levels for agent reasoning."""
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


@dataclass
class ThinkingConfig:
    """Configuration for a thinking level."""
    level: ThinkingLevel
    token_budget: int  # Max tokens for thinking
    system_prompt_suffix: str  # Added to system prompt
    description: str
    
    @property
    def label(self) -> str:
        return self.level.value.upper()


# Default thinking configurations
THINKING_CONFIGS: dict[ThinkingLevel, ThinkingConfig] = {
    ThinkingLevel.OFF: ThinkingConfig(
        level=ThinkingLevel.OFF,
        token_budget=0,
        system_prompt_suffix="",
        description="No explicit reasoning - respond directly",
    ),
    ThinkingLevel.MINIMAL: ThinkingConfig(
        level=ThinkingLevel.MINIMAL,
        token_budget=500,
        system_prompt_suffix="\nThink briefly before responding.",
        description="Brief reasoning for simple tasks",
    ),
    ThinkingLevel.LOW: ThinkingConfig(
        level=ThinkingLevel.LOW,
        token_budget=1000,
        system_prompt_suffix="\nThink step-by-step but keep it concise.",
        description="Light reasoning for straightforward tasks",
    ),
    ThinkingLevel.MEDIUM: ThinkingConfig(
        level=ThinkingLevel.MEDIUM,
        token_budget=2000,
        system_prompt_suffix="\nThink through this carefully, considering multiple approaches.",
        description="Moderate reasoning for complex tasks",
    ),
    ThinkingLevel.HIGH: ThinkingConfig(
        level=ThinkingLevel.HIGH,
        token_budget=4000,
        system_prompt_suffix="\nThink deeply and thoroughly. Consider edge cases, alternatives, and potential issues.",
        description="Deep reasoning for very complex tasks",
    ),
    ThinkingLevel.XHIGH: ThinkingConfig(
        level=ThinkingLevel.XHIGH,
        token_budget=8000,
        system_prompt_suffix="\nThink extremely thoroughly. Analyze every aspect, consider all edge cases, and provide comprehensive reasoning.",
        description="Maximum reasoning for critical tasks",
    ),
}


class ThinkingManager:
    """Manage thinking levels and reasoning depth."""
    
    def __init__(self, default_level: ThinkingLevel = ThinkingLevel.MEDIUM):
        self.current_level = default_level
        self.configs = dict(THINKING_CONFIGS)
        self.history: list[ThinkingLevel] = []
    
    @property
    def config(self) -> ThinkingConfig:
        """Get the current thinking configuration."""
        return self.configs[self.current_level]
    
    def set_level(self, level: ThinkingLevel | str) -> None:
        """Set the thinking level.
        
        Args:
            level: The thinking level to set.
        """
        if isinstance(level, str):
            level = ThinkingLevel(level.lower())
        
        self.history.append(self.current_level)
        self.current_level = level
    
    def get_level(self) -> ThinkingLevel:
        """Get the current thinking level."""
        return self.current_level
    
    def get_config(self, level: ThinkingLevel | None = None) -> ThinkingConfig:
        """Get configuration for a specific level."""
        if level is None:
            level = self.current_level
        return self.configs[level]
    
    def get_system_prompt_suffix(self) -> str:
        """Get the system prompt suffix for the current level."""
        return self.config.system_prompt_suffix
    
    def get_token_budget(self) -> int:
        """Get the token budget for the current level."""
        return self.config.token_budget
    
    def suggest_level(self, task_complexity: float) -> ThinkingLevel:
        """Suggest a thinking level based on task complexity.
        
        Args:
            task_complexity: Complexity score from 0.0 to 1.0.
        
        Returns:
            Recommended thinking level.
        """
        if task_complexity < 0.2:
            return ThinkingLevel.OFF
        elif task_complexity < 0.4:
            return ThinkingLevel.MINIMAL
        elif task_complexity < 0.6:
            return ThinkingLevel.LOW
        elif task_complexity < 0.8:
            return ThinkingLevel.MEDIUM
        elif task_complexity < 0.9:
            return ThinkingLevel.HIGH
        else:
            return ThinkingLevel.XHIGH
    
    def cycle_level(self, direction: int = 1) -> ThinkingLevel:
        """Cycle to the next/previous thinking level.
        
        Args:
            direction: 1 for next, -1 for previous.
        
        Returns:
            The new thinking level.
        """
        levels = list(ThinkingLevel)
        current_idx = levels.index(self.current_level)
        new_idx = (current_idx + direction) % len(levels)
        self.set_level(levels[new_idx])
        return self.current_level
    
    def reset(self) -> None:
        """Reset to the default level."""
        self.current_level = ThinkingLevel.MEDIUM
        self.history.clear()
    
    def format_level(self, level: ThinkingLevel | None = None) -> str:
        """Format a thinking level for display."""
        config = self.get_config(level)
        return f"{config.label} - {config.description}"
    
    def format_current(self) -> str:
        """Format the current thinking level."""
        return self.format_level()
    
    def list_levels(self) -> list[str]:
        """List all available thinking levels."""
        return [self.format_level(level) for level in ThinkingLevel]
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict."""
        return {
            "current_level": self.current_level.value,
            "history": [level.value for level in self.history],
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThinkingManager:
        """Deserialize from a dict."""
        manager = cls()
        manager.current_level = ThinkingLevel(data.get("current_level", "medium"))
        manager.history = [ThinkingLevel(level) for level in data.get("history", [])]
        return manager


# Global instance
_manager: ThinkingManager | None = None


def get_thinking_manager() -> ThinkingManager:
    """Get the global thinking manager instance."""
    global _manager
    if _manager is None:
        _manager = ThinkingManager()
    return _manager
