"""Advanced Terminal AI Agent package."""

__version__ = "1.0.0"

from .branching import ConversationTree, get_conversation_tree
from .config import Config
from .context_injector import ContextInjector, get_context_injector
from .core import Agent
from .export_html import export_session_to_html, export_to_html
from .headless import HeadlessOptions, HeadlessResult, run_headless
from .latency import TurnLatencyTracker, get_latency_tracker
from .memory import Conversation
from .progress import ProgressTracker
from .session_manager import SessionManager, get_session_manager
from .templates import TemplateManager, get_template_manager
from .thinking import ThinkingLevel, ThinkingManager, get_thinking_manager
from .tips import ContextualTips, get_contextual_tips
from .tools import ToolRegistry

__all__ = [
    "__version__",
    "Agent",
    "Config",
    "Conversation",
    "ToolRegistry",
    "HeadlessOptions",
    "HeadlessResult",
    "run_headless",
    "SessionManager",
    "get_session_manager",
    "ProgressTracker",
    "ContextInjector",
    "get_context_injector",
    "ConversationTree",
    "get_conversation_tree",
    "TurnLatencyTracker",
    "get_latency_tracker",
    "ContextualTips",
    "get_contextual_tips",
    "TemplateManager",
    "get_template_manager",
    "ThinkingManager",
    "get_thinking_manager",
    "ThinkingLevel",
    "export_to_html",
    "export_session_to_html",
]
