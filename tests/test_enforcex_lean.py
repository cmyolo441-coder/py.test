import os

from fullagent import enforcex
from fullagent.enforcex import loader


def test_loader_order_and_cache(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "system_prompt.txt").write_text("from home")
    monkeypatch.setenv("FULLAGENT_HOME", str(home))
    monkeypatch.delenv("FULLAGENT_SYSTEM_PROMPT_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "system_prompt.txt").write_text("from cwd")
    assert enforcex.find_user_prompt() == ("from home", str(home / "system_prompt.txt"))
    env = tmp_path / "env.txt"
    env.write_bytes(b"\xef\xbb\xbfa\r\nb")
    monkeypatch.setenv("FULLAGENT_SYSTEM_PROMPT_FILE", str(env))
    assert enforcex.find_user_prompt() == ("a\nb", str(env))
    # cache invalidates on change
    env.write_text("changed text")
    assert enforcex.find_user_prompt()[0] == "changed text"
    # missing env file falls through
    monkeypatch.setenv("FULLAGENT_SYSTEM_PROMPT_FILE", str(tmp_path / "nope"))
    assert enforcex.find_user_prompt()[0] == "from home"


def test_loader_cache_hit(tmp_path, monkeypatch):
    f = tmp_path / "p.txt"
    f.write_text("Always x y z")
    monkeypatch.setenv("FULLAGENT_SYSTEM_PROMPT_FILE", str(f))
    enforcex.find_user_prompt()
    n = len(loader._CACHE)
    enforcex.find_user_prompt()
    assert len(loader._CACHE) == n


def test_rules_extraction():
    t = "intro\n1. Always reply in English.\n* Never exceed 100 words.\nrandom\n"
    assert enforcex.extract_rules(t) == ["Always reply in English.", "Never exceed 100 words."]
    assert len(enforcex.extract_rules("always do it\n" * 3 + "\n".join(f"Never do {i}" for i in range(99)), 10)) == 10
    c = enforcex.checklist(t)
    assert len(c) < 1500 and "Always reply in English." in c
    assert c.upper() != c


def test_compose_verbatim_prefix():
    up = "  Weird   spacing\r\nAlways be kind.\n"
    out = enforcex.compose(up, {"ctx": "x"})
    assert out.startswith(up)
    assert out.index("[ctx]") < out.index("Reminder of your instructions")
    assert enforcex.compose(up, None).startswith(up)
    assert enforcex.__version__ == "3.0.0"
