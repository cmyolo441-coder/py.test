"""Prompt template library — delegates to systemprompts.py."""
from __future__ import annotations

from dataclasses import dataclass, field

from .systemprompts import PROMPT_TEMPLATES as _RAW_TEMPLATES


@dataclass
class PromptTemplate:
    name: str
    description: str
    category: str
    template: str
    placeholders: list[str] = field(default_factory=list)


TEMPLATES: list[PromptTemplate] = [PromptTemplate(**t) for t in _RAW_TEMPLATES]


def list_templates(category: str | None = None) -> list[PromptTemplate]:
    if category:
        return [t for t in TEMPLATES if t.category == category]
    return TEMPLATES


def get_template(name: str) -> PromptTemplate | None:
    for t in TEMPLATES:
        if t.name == name:
            return t
    return None


def categories() -> list[str]:
    return sorted({t.category for t in TEMPLATES})


def render(template: PromptTemplate, **kwargs: str) -> str:
    result = template.template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value)
    return result
