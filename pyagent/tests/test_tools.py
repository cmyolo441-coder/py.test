"""Tests for the tool registry and built-in tools."""

from __future__ import annotations

from agent.tools import build_default_registry
from agent.tools.data_tools import csv_to_json, json_query
from agent.tools.encoding_tools import b64_decode, b64_encode, hash_text
from agent.tools.math_tools import calculate


def test_registry_has_tools():
    reg = build_default_registry()
    names = reg.names()
    assert len(names) > 10
    assert "run_shell" in names
    assert "read_file" in names
    assert "write_file" in names


def test_unknown_tool():
    reg = build_default_registry()
    result = reg.execute("nonexistent_tool", {})
    assert not result.success


def test_calculate():
    res = calculate("2 + 3 * 4")
    assert "14" in res.output


def test_b64_roundtrip():
    encoded = b64_encode("hello world")
    decoded = b64_decode(encoded.output.strip())
    assert "hello world" in decoded.output


def test_hash():
    res = hash_text("test", "sha256")
    assert res.success
    assert len(res.output) == 64


def test_csv_to_json():
    res = csv_to_json("name,age\nAlice,30\nBob,25")
    assert res.success
    assert "Alice" in res.output
