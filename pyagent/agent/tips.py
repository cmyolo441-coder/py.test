"""Contextual tips — provide helpful tips based on conversation context.

Inspired by gsd-pi's contextual-tips module, this module provides:
  - Detect user intent and provide relevant tips
  - Suggest commands and features
  - Provide best practices
  - Adaptive tips based on usage patterns
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Tip:
    """A contextual tip."""
    id: str
    category: str
    message: str
    trigger: str  # Pattern that triggers this tip
    priority: int = 0  # Higher = more important
    shown_count: int = 0
    max_shows: int = 3  # Don't show too often


# Built-in tips
TIPS: list[Tip] = [
    # Code tips
    Tip(
        id="code_testing",
        category="code",
        message="💡 Tip: Use /multi-agent to run parallel specialists for complex tasks",
        trigger=r"(?:build|create|implement|write)\s+(?:a\s+)?(?:function|class|module|script)",
        priority=10,
    ),
    Tip(
        id="code_review",
        category="code",
        message="💡 Tip: Use /multi-agent to review code for bugs and security issues",
        trigger=r"(?:review|check|audit|analyze)\s+(?:my\s+)?(?:code|function|class)",
        priority=10,
    ),
    Tip(
        id="code_debug",
        category="code",
        message="💡 Tip: Describe the error message for better debugging help",
        trigger=r"(?:debug|fix|error|bug|issue)",
        priority=5,
    ),
    
    # Performance tips
    Tip(
        id="perf_streaming",
        category="performance",
        message="💡 Tip: Streaming is enabled for faster responses. Disable with /stream if needed.",
        trigger=r"(?:slow|fast|speed|performance|latency)",
        priority=5,
    ),
    Tip(
        id="perf_compact",
        category="performance",
        message="💡 Tip: Auto-compaction keeps context lean. Long conversations are automatically compressed.",
        trigger=r"(?:context|token|memory|long\s+conversation)",
        priority=5,
    ),
    
    # Session tips
    Tip(
        id="session_save",
        category="session",
        message="💡 Tip: Use /save to save your conversation for later.",
        trigger=r"(?:save|exit|quit|bye|goodbye)",
        priority=10,
    ),
    Tip(
        id="session_resume",
        category="session",
        message="💡 Tip: Use /resume <id> to continue a previous session.",
        trigger=r"(?:resume|continue|previous|last\s+session)",
        priority=10,
    ),
    
    # Feature tips
    Tip(
        id="feature_orchestrate",
        category="feature",
        message="💡 Tip: /orchestrate auto-detects complex tasks and runs specialists in parallel.",
        trigger=r"(?:complex|big|large|multiple|several)",
        priority=5,
    ),
    Tip(
        id="feature_headless",
        category="feature",
        message="💡 Tip: Use /headless for CI/CD automation without the TUI.",
        trigger=r"(?:ci|cd|automate|script|batch)",
        priority=5,
    ),
    Tip(
        id="feature_sessions",
        category="feature",
        message="💡 Tip: /sessions shows all saved sessions you can resume.",
        trigger=r"(?:session|history|previous|past)",
        priority=5,
    ),
    
    # Best practices
    Tip(
        id="practice_specific",
        category="best_practice",
        message="💡 Tip: Be specific in your requests for better results.",
        trigger=r"(?:help|how|what|why|explain)",
        priority=3,
    ),
    Tip(
        id="practice_context",
        category="best_practice",
        message="💡 Tip: Mention file paths or function names for context-aware help.",
        trigger=r"(?:file|function|class|module|method)",
        priority=3,
    ),
]


class ContextualTips:
    """Provide contextual tips based on conversation context."""
    
    def __init__(self):
        self.tips: list[Tip] = list(TIPS)
        self.shown_tips: dict[str, int] = {}
        self.user_patterns: dict[str, int] = {}
    
    def _matches_trigger(self, text: str, trigger: str) -> bool:
        """Check if text matches a trigger pattern."""
        try:
            return bool(re.search(trigger, text, re.IGNORECASE))
        except re.error:
            return False
    
    def get_tips(
        self,
        user_input: str,
        context: dict[str, Any] | None = None,
        max_tips: int = 3,
    ) -> list[Tip]:
        """Get relevant tips for the current context.
        
        Args:
            user_input: The user's input text.
            context: Additional context (conversation history, etc.).
            max_tips: Maximum tips to return.
        
        Returns:
            List of relevant tips.
        """
        candidates: list[tuple[int, Tip]] = []
        
        for tip in self.tips:
            # Check if already shown too many times
            shown = self.shown_tips.get(tip.id, 0)
            if shown >= tip.max_shows:
                continue
            
            # Check trigger match
            if self._matches_trigger(user_input, tip.trigger):
                # Adjust priority based on show count
                adjusted_priority = tip.priority - shown * 2
                candidates.append((adjusted_priority, tip))
        
        # Sort by priority
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Return top tips
        tips = [tip for _, tip in candidates[:max_tips]]
        
        # Update shown counts
        for tip in tips:
            self.shown_tips[tip.id] = self.shown_tips.get(tip.id, 0) + 1
        
        return tips
    
    def get_tip_by_id(self, tip_id: str) -> Tip | None:
        """Get a specific tip by ID."""
        for tip in self.tips:
            if tip.id == tip_id:
                return tip
        return None
    
    def add_tip(self, tip: Tip) -> None:
        """Add a custom tip."""
        self.tips.append(tip)
    
    def remove_tip(self, tip_id: str) -> bool:
        """Remove a tip by ID."""
        for i, tip in enumerate(self.tips):
            if tip.id == tip_id:
                self.tips.pop(i)
                return True
        return False
    
    def reset_show_counts(self) -> None:
        """Reset all show counts."""
        self.shown_tips.clear()
    
    def format_tips(self, tips: list[Tip]) -> str:
        """Format tips for display."""
        if not tips:
            return ""
        
        lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for tip in tips:
            lines.append(tip.message)
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        return "\n".join(lines)
    
    def update_patterns(self, user_input: str) -> None:
        """Update user patterns based on input.
        
        This helps personalize tips over time.
        """
        # Extract common words
        words = user_input.lower().split()
        for word in words:
            if len(word) > 3:  # Skip short words
                self.user_patterns[word] = self.user_patterns.get(word, 0) + 1


# Global instance
_tips: ContextualTips | None = None


def get_contextual_tips() -> ContextualTips:
    """Get the global contextual tips instance."""
    global _tips
    if _tips is None:
        _tips = ContextualTips()
    return _tips
