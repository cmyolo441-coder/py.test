"""Tests for text, random and data-processing tools."""

from __future__ import annotations

from agent.tools.random_tools import gen_password, gen_uuid, random_choice, roll_dice
from agent.tools.text_tools import change_case, dedupe_lines, sort_lines, text_diff, text_stats


def test_text_stats():
    res = text_stats("hello world\nhello")
    assert "words: 3" in res.output
    assert "lines: 2" in res.output


def test_change_case():
    assert change_case("abc", "upper").output == "ABC"
    assert change_case("ABC", "lower").output == "abc"
    assert change_case("abc", "nonsense").success is False


def test_sort_lines_unique():
    res = sort_lines("b\na\nb", unique=True)
    assert res.output == "a\nb"


def test_dedupe_lines():
    res = dedupe_lines("x\ny\nx\nz\ny")
    assert res.output == "x\ny\nz"
    assert res.metadata["kept"] == 3


def test_text_diff_identical():
    assert text_diff("same", "same").output == "(identical)"


def test_gen_uuid_count():
    res = gen_uuid(3)
    assert len(res.output.splitlines()) == 3


def test_gen_password_length():
    res = gen_password(20, symbols=False)
    assert len(res.output) == 20


def test_roll_dice_range():
    res = roll_dice(6, 5)
    assert "sum=" in res.output


def test_random_choice_empty():
    assert random_choice([]).success is False


def test_random_choice_picks():
    assert random_choice(["only"]).output == "only"
