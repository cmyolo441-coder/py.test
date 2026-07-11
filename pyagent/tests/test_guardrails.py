"""Tests for guardrails and personas."""

from __future__ import annotations

from agent.guardrails import Guardrails
from agent.personas import PERSONAS, get_prompt, list_personas


def test_guardrail_allows_safe():
    g = Guardrails()
    g.start_turn()
    d = g.check("run_shell", {"command": "ls -la"})
    assert d.allow is True
    assert d.force_confirm is False


def test_guardrail_flags_dangerous():
    g = Guardrails()
    g.start_turn()
    d = g.check("run_shell", {"command": "rm -rf /"})
    assert d.allow is True
    assert d.force_confirm is True


def test_guardrail_budget():
    g = Guardrails(max_calls_per_turn=2)
    g.start_turn()
    assert g.check("now", {}).allow is True
    assert g.check("now", {}).allow is True
    assert g.check("now", {}).allow is False


def test_personas_present():
    assert "coder" in PERSONAS
    assert get_prompt("CODER") is not None
    assert get_prompt("nope") is None
    assert len(list_personas()) >= 4
