"""Tests for search and edit tools using a temp directory."""

from __future__ import annotations

from pathlib import Path

from agent.tools.search_tools import find_files, grep, count_lines
from agent.tools.edit_tools import view_lines, replace_in_file, insert_line, delete_lines


def _make_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_find_files(tmp_path: Path):
    _make_file(tmp_path, "a.txt", "x")
    _make_file(tmp_path, "b.py", "y")
    res = find_files("*.py", str(tmp_path))
    assert "b.py" in res.output


def test_grep(tmp_path: Path):
    _make_file(tmp_path, "log.txt", "line1\nERROR here\nline3")
    res = grep("ERROR", str(tmp_path))
    assert "ERROR here" in res.output


def test_count_lines(tmp_path: Path):
    p = _make_file(tmp_path, "c.txt", "a\nb\nc")
    assert count_lines(str(p)).metadata["lines"] == 3


def test_view_lines(tmp_path: Path):
    p = _make_file(tmp_path, "v.txt", "one\ntwo\nthree")
    res = view_lines(str(p), 2, 3)
    assert "two" in res.output
    assert "one" not in res.output


def test_replace_in_file(tmp_path: Path):
    p = _make_file(tmp_path, "r.txt", "hello world")
    replace_in_file(str(p), "world", "there")
    assert p.read_text(encoding="utf-8") == "hello there"


def test_replace_missing(tmp_path: Path):
    p = _make_file(tmp_path, "r2.txt", "abc")
    assert replace_in_file(str(p), "zzz", "y").success is False


def test_insert_line(tmp_path: Path):
    p = _make_file(tmp_path, "i.txt", "a\nc")
    insert_line(str(p), 2, "b")
    assert p.read_text(encoding="utf-8").splitlines() == ["a", "b", "c"]


def test_delete_lines(tmp_path: Path):
    p = _make_file(tmp_path, "d.txt", "a\nb\nc\nd")
    delete_lines(str(p), 2, 3)
    assert p.read_text(encoding="utf-8").splitlines() == ["a", "d"]
