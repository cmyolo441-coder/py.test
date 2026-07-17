"""Tests for the command registry."""

from __future__ import annotations

from agent.commands import build_command_registry


def test_command_registry_resolves():
    reg = build_command_registry()
    assert reg.get("/exit") is not None
    assert reg.get("/help") is not None
    assert reg.get("/tools") is not None


def test_command_aliases():
    reg = build_command_registry()
    assert reg.get("/quit") is not None
    assert reg.get("/q") is not None
    assert reg.get("/?") is not None
