"""enforcex: loader reads systemprompt.py only; rules/compose/follow."""
import pytest

from fullagent import systemprompt
from fullagent.enforcex import (checklist, compose, extract_rules,
                                find_user_prompt, compile_rules, check,
                                repair_note)


@pytest.fixture()
def slot():
    yield systemprompt.set_user_prompt
    systemprompt.set_user_prompt("")


def test_loader_reads_only_systemprompt_slot(slot):
    slot("")
    assert find_user_prompt() == ("", None)
    slot("Always answer in French.")
    text, path = find_user_prompt()
    assert text == "Always answer in French."
    assert path.endswith("systemprompt.py")


def test_slot_text_is_stripped_and_newlines_normalised(slot):
    slot("\r\n  hello\r\nworld  \r\n")
    assert systemprompt.USER_PROMPT == "hello\nworld"


def test_rules_and_checklist():
    t = "Intro.\n1. Always answer in French.\n- Never use emojis.\n- Never use emojis."
    assert extract_rules(t) == ["Always answer in French.", "Never use emojis."]
    assert checklist(t).startswith("Reminder of your instructions:")
    assert checklist("nothing") == ""


def test_compose_keeps_prompt_verbatim_first():
    p = "  RAW  prompt\nline2 "
    out = compose(p, {"goal": "g"})
    assert out.startswith(p) and "g" in out


PROMPT = ('Never say "As an AI". Always respond in Hinglish only. '
          'Keep every reply under 20 words. Do not use emojis. '
          'Start every reply with "Boss,".')


def test_follow_compiles_and_checks():
    rules = compile_rules(PROMPT)
    assert {r.kind for r in rules} >= {"forbid", "lang_latin", "max_words",
                                       "no_emoji", "starts"}
    assert check("Boss, ho gaya.", rules) == []
    bad = check("As an AI, यह हो गया 😀", rules)
    assert {v.rule.kind for v in bad} >= {"forbid", "lang_latin",
                                          "no_emoji", "starts"}
    assert "Rewrite" in repair_note(bad)


def test_follow_ignores_code_blocks_and_empty():
    rules = compile_rules(PROMPT)
    assert check("Boss, dekho:\n```\nAs an AI 😀\n```", rules) == []
    assert check("", rules) == [] and check("x", ()) == []
