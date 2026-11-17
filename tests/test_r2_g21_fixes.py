import json
import os
import re
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import plugins  # noqa: E402








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
