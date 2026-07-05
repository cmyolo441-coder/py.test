"""Tests for session storage and export."""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent.session import Session, SessionStore, export_markdown, export_json


def test_session_roundtrip():
    s = Session(title="Test", provider="zen", model="mimo-v2.5-free")
    s.add("user", "hi")
    s.add("assistant", "hello")
    data = s.to_dict()
    restored = Session.from_dict(data)
    assert restored.title == "Test"
    assert restored.message_count == 2


def test_session_store(tmp_path: Path):
    store = SessionStore(directory=tmp_path)
    s = Session(title="Persisted")
    store.save(s)
    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.title == "Persisted"
    assert store.delete(s.id) is True
    assert store.load(s.id) is None


def test_export_markdown(tmp_path: Path):
    s = Session(title="Export test")
    s.add("user", "question")
    s.add("assistant", "answer")
    out = export_markdown(s, tmp_path / "out.md")
    content = out.read_text(encoding="utf-8")
    assert "Export test" in content
    assert "answer" in content


def test_export_json(tmp_path: Path):
    s = Session(title="JSON test")
    out = export_json(s, tmp_path / "out.json")
    assert out.exists()
    assert "JSON test" in out.read_text(encoding="utf-8")
