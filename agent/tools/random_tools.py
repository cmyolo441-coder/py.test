"""Random / generation tools: UUIDs, passwords, lorem, dice, choice."""

from __future__ import annotations

import random
import secrets
import string
import uuid

from .base import Tool, ToolResult

_LOREM = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim veniam"
).split()


def gen_uuid(count: int = 1) -> ToolResult:
    count = max(1, min(count, 100))
    return ToolResult(output="\n".join(str(uuid.uuid4()) for _ in range(count)))


def gen_password(length: int = 16, symbols: bool = True) -> ToolResult:
    length = max(4, min(length, 256))
    alphabet = string.ascii_letters + string.digits + ("!@#$%^&*()-_=+" if symbols else "")
    return ToolResult(output="".join(secrets.choice(alphabet) for _ in range(length)))


def gen_lorem(words: int = 30) -> ToolResult:
    words = max(1, min(words, 500))
    out = [random.choice(_LOREM) for _ in range(words)]
    out[0] = out[0].capitalize()
    return ToolResult(output=" ".join(out) + ".")


def roll_dice(sides: int = 6, count: int = 1) -> ToolResult:
    sides = max(2, sides)
    count = max(1, min(count, 100))
    rolls = [random.randint(1, sides) for _ in range(count)]
    return ToolResult(output=f"{rolls} (sum={sum(rolls)})")


def random_choice(options: list[str]) -> ToolResult:
    if not options:
        return ToolResult(output="No options provided", success=False)
    return ToolResult(output=secrets.choice(options))


def get_random_tools() -> list[Tool]:
    return [
        Tool("gen_uuid", "Generate one or more random UUID4 values.",
             {"type": "object", "properties": {"count": {"type": "integer", "default": 1}}}, gen_uuid),
        Tool("gen_password", "Generate a cryptographically secure random password.",
             {"type": "object", "properties": {
                 "length": {"type": "integer", "default": 16},
                 "symbols": {"type": "boolean", "default": True}}}, gen_password),
        Tool("gen_lorem", "Generate lorem-ipsum placeholder text.",
             {"type": "object", "properties": {"words": {"type": "integer", "default": 30}}}, gen_lorem),
        Tool("roll_dice", "Roll dice with the given number of sides.",
             {"type": "object", "properties": {
                 "sides": {"type": "integer", "default": 6},
                 "count": {"type": "integer", "default": 1}}}, roll_dice),
        Tool("random_choice", "Pick a random item from a list of options.",
             {"type": "object", "properties": {
                 "options": {"type": "array", "items": {"type": "string"}}}, "required": ["options"]}, random_choice),
    ]
