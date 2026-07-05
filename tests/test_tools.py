"""Tests for the tool registry and a sampling of built-in tools."""

from __future__ import annotations

from agent.tools import build_default_registry
from agent.tools.math_tools import calculate
from agent.tools.encoding_tools import b64_encode, b64_decode, hash_text
from agent.tools.data_tools import json_query, csv_to_json


def test_registry_loads_many_tools():
    reg = build_default_registry()
    assert len(reg.all()) >= 40
    assert reg.get("run_shell") is not None
    assert reg.get("calculate") is not None


def test_calculate_basic():
    assert calculate("2 + 3 * 4").output == "14"
    assert calculate("sqrt(16)").output == "4.0"


def test_calculate_rejects_bad_input():
    res = calculate("__import__('os')")
    assert res.success is False


def test_base64_roundtrip():
    enc = b64_encode("hello world").output
    dec = b64_decode(enc).output
    assert dec == "hello world"


def test_hash_text():
    res = hash_text("abc", "sha256")
    assert res.output == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_json_query_path():
    data = '{"a": {"b": [10, 20, 30]}}'
    assert json_query(data, "a.b.1").output == "20"


def test_csv_to_json():
    res = csv_to_json("name,age\nAlice,30\nBob,25")
    assert res.metadata["rows"] == 2


def test_registry_execute_unknown_tool():
    reg = build_default_registry()
    res = reg.execute("does_not_exist", {})
    assert res.success is False
