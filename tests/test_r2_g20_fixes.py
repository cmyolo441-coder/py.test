"""Regression tests for round-2 group-20 audit fixes."""
import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from fullagent import adherence, bgsh, dashboard, forge, hierarchy, judge  # noqa: E402
from fullagent.enforcex import redteam  # noqa: E402
from fullagent.kernel import EventLog, fold  # noqa: E402


def test_redteam_single_wave_string_runs_only_that_wave():
    rules = [{"id": "R1", "text": "Never share API keys."}]
    waves = redteam.generate_scenarios(rules, waves=redteam.WAVES.AUTHORITY)
    assert len(waves) == 1
    assert all(a.wave == redteam.WAVES.AUTHORITY for a in waves[0])


def test_judge_diff_assert_string_pattern_is_one_regex(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("TOTAL = 1\n")  # contains T, O, D but not "TODO"
    v = judge.check_diff_assert(str(f), forbid="TODO")
    assert v.passed, v
    v = judge.check_diff_assert(str(f), require="TOTAL")
    assert v.passed, v
    v = judge.check_diff_assert(str(f), require="MISSING")
    assert not v.passed


def test_bgsh_rejects_nan_timeout():
    with pytest.raises(ValueError):
        bgsh.launch("true", "x", float("nan"))


def test_bgsh_tail_lines_overflow_returns_error():
    tid = bgsh.launch("true", "x", 10)
    out = bgsh._handle_bash_output(task_id=tid, tail_lines=float("inf"))
    assert out.startswith("ERROR")


def test_hierarchy_topological_order_ignores_unknown_ids():
    g = hierarchy.RuleGraph()
    g.add(hierarchy.Directive("A", 0, "t", "*", "session", 0))
    g.link("X", "A", "depends")   # X never added
    g.link("A", "Y", "depends")   # Y never added
    assert g.topological_order() == ["A"]


def test_dashboard_malformed_findings_and_inf_entities(tmp_path):
    log = EventLog(Path(tmp_path) / "d.jsonl")
    log.append("analysis.taint", {"findings": 3})
    log.append("graph.entity", {"entities": float("inf")})
    d = dashboard.Dashboard(log)
    text = d.render()
    assert "ENGINE taint 0" in text
    assert d.snapshot()["graph_entities"] == 0
    assert any(t["summary"].startswith("taint 0") for t in d.tail())


def test_forge_probe_survives_non_utf8_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LANG", "en_US.\udcff")
    log = EventLog(Path(tmp_path) / "f.jsonl")
    f = forge.Forge(log, cwd=tmp_path)
    rec = f.probe()
    assert rec["digest"] and len(fold(log).env_digests) == 1


@pytest.mark.parametrize("line", ["END OF CONTEXT", "WRAP-ID v2: abc",
                                  "PRE-RESPONSE CHECK now"])
def test_adherence_seal_markers_are_defused(line):
    body, flags = adherence.sanitize_section("intro\n" + line)
    assert "fake-tag-defused" in flags
    assert not adherence._FAKE_TAG_RE.search(body)
    assert not adherence.detect_seal_spoof(body)
