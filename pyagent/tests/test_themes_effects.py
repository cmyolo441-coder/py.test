"""Tests for the theme system and visual effects."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from agent import effects, themes


def test_default_theme_active():
    assert themes.set_theme(themes.DEFAULT_THEME)
    assert themes.current().name == themes.DEFAULT_THEME


def test_all_builtin_themes_switch():
    for name in themes.names():
        assert themes.set_theme(name)
        assert themes.current().name == name
        # Gradient must always resolve to at least two stops.
        assert len(themes.current().grad()) >= 2


def test_unknown_theme_rejected():
    assert not themes.set_theme("does-not-exist")


def test_shorthand_hex_parsing():
    # cyberpunk uses #f0f / #0ff shorthand hex.
    assert themes.set_theme("cyberpunk")
    color = effects.blend(themes.current().accent, themes.current().accent2, 0.5)
    assert color.startswith("#") and len(color) == 7


def test_gradient_text_covers_all_chars():
    themes.set_theme("neon")
    t = effects.heading("HELLO")
    assert isinstance(t, Text)
    assert t.plain == "HELLO"


def test_progress_bar_bounds():
    for frac in (-1.0, 0.0, 0.5, 1.0, 2.0):
        bar = effects.progress_bar(frac, width=10)
        assert isinstance(bar, Text)
        assert "%" in bar.plain


def test_thinking_frame_all_spinners():
    themes.set_theme("matrix")
    for spinner in effects.SPINNERS:
        frame = effects.thinking_frame(5, spinner=spinner)
        assert isinstance(frame, Text)
        assert frame.plain.strip()


def test_effects_render_without_error():
    # Non-terminal console: animations should degrade gracefully.
    console = Console(force_terminal=False, width=80)
    console.print(effects.progress_bar(0.5, label="x"))
    console.print(effects.heading("banner"))
