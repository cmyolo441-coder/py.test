from agent.yaml_config import DEFAULTS, load_config, _deep_merge


def test_defaults_present():
    cfg = load_config("nonexistent-file.yaml")
    assert cfg["provider"] == DEFAULTS["provider"]
    assert "theme" in cfg


def test_deep_merge():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    override = {"a": 2, "nested": {"y": 3}}
    merged = _deep_merge(base, override)
    assert merged["a"] == 2
    assert merged["nested"] == {"x": 1, "y": 3}


def test_env_override(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    cfg = load_config("nonexistent-file.yaml")
    assert cfg["provider"] == "anthropic"
