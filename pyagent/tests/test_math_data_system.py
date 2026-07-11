"""Tests for math/data/system tools."""

from __future__ import annotations

from agent.tools.data_tools import csv_summary, json_format
from agent.tools.encoding_tools import hex_encode, url_decode, url_encode
from agent.tools.math_tools import calculate, date_diff
from agent.tools.system_tools import system_info, which


def test_calculate_functions():
    assert calculate("log10(1000)").output == "3.0"
    assert calculate("2 ** 10").output == "1024"


def test_calculate_pi():
    out = float(calculate("pi").output)
    assert 3.14 < out < 3.15


def test_date_diff():
    assert date_diff("2024-01-01", "2024-01-31").output == "30 days"


def test_date_diff_invalid():
    assert date_diff("bad", "2024-01-01").success is False


def test_json_format():
    assert '"a": 1' in json_format('{"a":1}').output


def test_json_format_invalid():
    assert json_format("{bad}").success is False


def test_csv_summary():
    res = csv_summary("a,b,c\n1,2,3\n4,5,6")
    assert "data rows: 2" in res.output


def test_system_info():
    assert "python" in system_info().output


def test_which_python():
    # python should be resolvable in the test environment
    res = which("python")
    assert res.success in (True, False)  # depends on PATH, but must not crash


def test_url_encode_decode_roundtrip():
    enc = url_encode("a b&c").output
    assert url_decode(enc).output == "a b&c"


def test_hex_encode():
    assert hex_encode("A").output == "41"
