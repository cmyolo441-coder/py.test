"""Tests for the plugin loader."""

from __future__ import annotations

from pathlib import Path

from agent.plugins import loader
from agent.tools.base import Tool


def test_load_plugins_from_dir(tmp_path: Path, monkeypatch):
    plugin_code = '''
from agent.tools.base import Tool, ToolResult

def _echo(text: str):
    return ToolResult(output=text)

def register():
    return [Tool("echo_plugin", "echo", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, _echo)]
'''
    (tmp_path / "myplugin.py").write_text(plugin_code, encoding="utf-8")
    monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)

    tools = loader.load_plugins()
    names = [t.name for t in tools]
    assert "echo_plugin" in names
    assert all(isinstance(t, Tool) for t in tools)


def test_load_plugins_missing_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path / "does_not_exist")
    assert loader.load_plugins() == []


def test_bad_plugin_is_skipped(tmp_path: Path, monkeypatch):
    (tmp_path / "broken.py").write_text("this is not valid python !!!", encoding="utf-8")
    monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
    # Should not raise, just skip the broken plugin.
    assert loader.load_plugins() == []
