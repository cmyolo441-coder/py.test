"""Prompt templates — delegates to systemprompts.py."""
from __future__ import annotations

from .systemprompts import BASE, PERSONAS


def get_persona(name: str) -> str:
    return PERSONAS.get(name.lower(), BASE)


def list_personas() -> list[str]:
    return sorted(PERSONAS)


def render_template(template: str, **variables: str) -> str:
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", value)
    return result
