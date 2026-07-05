"""Tests for utility helpers."""

from __future__ import annotations

from agent.utils.text import truncate_middle, slugify, strip_ansi, first_line
from agent.utils.files import human_size
from agent.utils.tokens import estimate_tokens
from agent.utils.security import assess_command, redact_secrets
from agent.utils.timing import Timer


def test_truncate_middle():
    text = "x" * 100
    out = truncate_middle(text, max_len=40)
    assert len(out) <= 60
    assert "truncated" in out


def test_slugify():
    assert slugify("Hello, World! 123") == "hello-world-123"


def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"


def test_first_line():
    assert first_line("line one\nline two") == "line one"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1024 * 1024) == "1.0 MB"


def test_estimate_tokens_positive():
    assert estimate_tokens("hello world this is a test") > 0
    assert estimate_tokens("") == 0


def test_assess_command_flags_danger():
    danger, reason = assess_command("rm -rf /")
    assert danger is True
    assert reason is not None


def test_assess_command_safe():
    danger, _ = assess_command("ls -la")
    assert danger is False


def test_redact_secrets():
    text = "my key is sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    assert "sk-***" in redact_secrets(text)


def test_timer():
    with Timer() as t:
        pass
    assert t.elapsed >= 0
    assert isinstance(t.pretty, str)
