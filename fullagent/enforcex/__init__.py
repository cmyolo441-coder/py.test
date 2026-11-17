"""enforcex: makes the agent follow the user's system prompt.

The prompt itself lives only in fullagent/systemprompt.py (SYSTEM_PROMPT).
  loader   reads it from there
  rules    cheap instruction checklist appended once at the end (recency)
  compose  prompt verbatim first, extras after
  follow   compiles hard, checkable rules and verifies replies (no model call)
"""
from .loader import find_user_prompt
from .rules import checklist, extract_rules
from .compose import compose
from .follow import compile_rules, check, repair_note

__version__ = "3.1.0"
__all__ = ["find_user_prompt", "extract_rules", "checklist", "compose",
           "compile_rules", "check", "repair_note", "__version__"]
