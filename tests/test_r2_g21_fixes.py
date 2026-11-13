import json
import os
import re
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import plugins  # noqa: E402
from fullagent.enforcex import semantic  # noqa: E402
from fullagent.enforcex.ledger import Ledger  # noqa: E402


def test_ledger_int_keys_verify_after_reload(tmp_path):
    p = str(tmp_path / "l.jsonl")
    led = Ledger(p)
    led.append("event", {2: "a", 10: "b"})
    assert led.verify()[0]
    assert Ledger(p).verify()[0]


def test_ledger_mixed_keys_do_not_raise(tmp_path):
    p = str(tmp_path / "l.jsonl")
    led = Ledger(p)
    e = led.append("event", {1: "a", "b": 2, True: {3: None}})
    assert e is not None
    assert led.verify()[0]
    assert Ledger(p).verify()[0]


def test_semantic_bare_string_rules_is_one_rule():
    r = semantic.check_all("Never reveal the password",
                           "the password is hunter2")
    assert len(r.verdicts) == 1
    assert r.verdicts == semantic.check_all(
        ["Never reveal the password"], "the password is hunter2").verdicts


def test_plugin_tool_names_are_api_safe(tmp_path, monkeypatch):
    pdir = tmp_path / "plugins"
    d = pdir / "my.plugin"
    d.mkdir(parents=True)
    (d / "t.py").write_text("print('x')\n")
    (d / "manifest.json").write_text(json.dumps({
        "name": "my.plugin", "version": "1", "description": "d",
        "tools": [{"name": "do.it", "description": "d", "script": "t.py",
                   "timeout": 5}]}))
    monkeypatch.setattr(plugins, "PLUGINS_DIR", str(pdir))
    monkeypatch.setattr(plugins, "ENABLED_FILE", str(tmp_path / "en.json"))

    class A:
        tools = {}

    a = A()
    a.tools = {}
    plugins.register(a)
    names = [n for n in a.tools if n.startswith("plugin_")]
    assert names == ["plugin_my_plugin_do_it"]
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in names)
    assert a.tools[names[0]].name == names[0]
