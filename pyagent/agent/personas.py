"""System-prompt presets — delegates to systemprompts.py."""
from __future__ import annotations

from .systemprompts import PERSONAS


def get_prompt(name: str) -> str | None:
    return PERSONAS.get(name.lower())


def list_personas() -> list[str]:
    return sorted(PERSONAS)
