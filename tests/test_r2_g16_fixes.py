import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

import pytest

from fullagent import sandbox, configcmd, progress
from fullagent.autopilot import AutoPilot
from fullagent.enforcex.cognify import CognitiveReport


def test_preexec_clamps_to_inherited_hard_limit(monkeypatch):
    import resource
    calls = []
    monkeypatch.setattr(resource, "getrlimit", lambda which: (30, 30))
    monkeypatch.setattr(resource, "setrlimit",
                        lambda which, v: calls.append((which, v)))
    fn = sandbox._make_preexec(120, 1024, 100)
    fn()
    assert calls and all(v == (30, 30) for _, v in calls)


def test_preexec_unlimited_hard_keeps_value(monkeypatch):
    import resource
    calls = []
    monkeypatch.setattr(resource, "getrlimit",
                        lambda which: (resource.RLIM_INFINITY,
                                       resource.RLIM_INFINITY))
    monkeypatch.setattr(resource, "setrlimit",
                        lambda which, v: calls.append((which, v)))
    sandbox._make_preexec(120, None, None)()
    assert calls == [(resource.RLIMIT_CPU, (120, 120))]


def test_config_set_rolls_back_on_persist_failure(tmp_path, monkeypatch):
    cfg = configcmd.UserConfig(str(tmp_path / "config.json"))

    def boom():
        raise PermissionError("read-only")
    monkeypatch.setattr(cfg, "_persist_locked", boom)
    with pytest.raises(PermissionError):
        cfg.set("vim_mode", "true")
    assert cfg.get("vim_mode") is False


def test_config_reset_rolls_back_on_persist_failure(tmp_path, monkeypatch):
    cfg = configcmd.UserConfig(str(tmp_path / "config.json"))
    cfg.set("vim_mode", "true")

    def boom():
        raise PermissionError("read-only")
    monkeypatch.setattr(cfg, "_persist_locked", boom)
    with pytest.raises(PermissionError):
        cfg.reset("vim_mode")
    assert cfg.get("vim_mode") is True


def test_progress_start_non_string_label():
    tr = progress.ProgressTracker()
    pid = tr.start(123, 5)
    assert tr.get(pid)["label"] == "123"
    assert tr.get(tr.start(None, 1))["label"] == "task"


def test_autopilot_latest_does_not_draft_test_clause(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    monkeypatch.chdir(tmp_path)
    ap = AutoPilot(log=None, enabled=True)
    clauses = ap._draft_clauses("fix the latest release notes")
    assert not any(c.get("proof", {}).get("type") == "exit_code"
                   for c in clauses)
    clauses = ap._draft_clauses("fix the failing tests")
    assert any(c.get("proof", {}).get("type") == "exit_code"
               for c in clauses)
    clauses = ap._draft_clauses("make pytest pass")
    assert clauses[0]["proof"]["type"] == "exit_code"


def test_cognitive_report_from_dict_null_signals():
    r = CognitiveReport.from_dict({"signals": None, "hijacked": True})
    assert r.signals == [] and r.hijacked is True
