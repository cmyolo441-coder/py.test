"""Tests for the command registry and effects rendering."""

from __future__ import annotations

from agent.commands import build_command_registry
from agent.effects import gradient_text, thinking_frame, blend, BANNER_ART


def test_command_registry_resolves():
    reg = build_command_registry()
    assert reg.resolve("/help") is not None
    assert reg.resolve("/quit") is not None  # alias of /exit
    assert reg.resolve("/nope") is None


def test_command_registry_has_many():
    reg = build_command_registry()
    assert len(reg.all()) >= 10


def test_gradient_text_preserves_chars():
    t = gradient_text("HELLO", ["#000000", "#ffffff"])
    assert t.plain == "HELLO"


def test_thinking_frame_returns_text():
    frame = thinking_frame(5)
    assert "\u2026" in frame.plain or "..." in frame.plain


def test_blend_midpoint():
    assert blend("#000000", "#ffffff", 0.5).startswith("#")


def test_banner_not_empty():
    assert len(BANNER_ART) > 50
