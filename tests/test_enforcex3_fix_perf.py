"""enforcex (area 3) performance / unbounded-growth regressions.

Covers drift, cognify, critic, semantics, warroom, telemetry and probes:
quadratic regexes, per-turn rescans and files/lists that grew forever.
All offline; app state isolated under a temp FULLAGENT_HOME.
"""

import os
import tempfile
import time

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="fa-ex3-"))

from fullagent.enforcex import (  # noqa: E402
    cognify, critic, drift, probes, semantics, telemetry, warroom,
)


def _timed(fn, *a, **kw):
    t = time.perf_counter()
    out = fn(*a, **kw)
    return out, time.perf_counter() - t


BLANKS = "x" + "\n" * 100_000 + "x"
WS_BLANKS = "x" + " \n" * 50_000 + "x"


def test_line_anchored_regexes_are_linear_on_blank_line_runs():
    # (?:^|\n)\s* let \s* cross newlines: O(n^2), >20 s on 20k chars.
    for text in (BLANKS, WS_BLANKS):
        for pat in drift._STRUCT_PATTERNS.values():
            _, dt = _timed(pat.findall, text)
            assert dt < 0.5, (pat.pattern, dt)
        for pat in (cognify._NUMBERED_RE, cognify._BULLET_RE,
                    critic.SECTION_RE, critic._LIST_ITEM_RE):
            _, dt = _timed(pat.findall, text)
            assert dt < 0.5, (pat.pattern, dt)
    _, dt = _timed(critic.SECTION_RE.findall, "# x" + " " * 100_000 + "x\n")
    assert dt < 0.5


def test_line_anchored_regexes_still_count_markdown():
    text = "# Title\n\n- one\n  * two\n1. three\n2) four\n> quote\n#nospace\n"
    v = drift._structural_vector(text)
    assert v["markdown_heading"] > 0 and v["bullet_list"] > 0
    assert v["numbered_list"] > 0 and v["blockquote"] > 0
    assert critic.SECTION_RE.findall(text) == ["Title", "nospace"]
    assert len(critic._LIST_ITEM_RE.findall(text)) == 4
    assert len(cognify._BULLET_RE.findall(text)) == 2
    assert len(cognify._NUMBERED_RE.findall(text)) == 4


def test_public_entry_points_fast_on_blank_line_input():
    rules = [{"id": "R1", "text": "Use a bullet list", "keywords": ["list"]}]
    _, dt = _timed(drift.scan, BLANKS, rules, "You are an agent.")
    assert dt < 2.0
    _, dt = _timed(critic.CriticEnsemble().verdict, BLANKS, rules)
    assert dt < 2.0
    _, dt = _timed(cognify.analyze_cognition, BLANKS, "sys", BLANKS)
    assert dt < 2.0


def test_drift_anchor_features_cached_per_turn():
    anchor = " ".join("word%d alpha beta." % i for i in range(8000))
    tr = drift.DriftTracker(anchor)
    tr.add_turn("warm up")
    _, dt = _timed(lambda: [tr.add_turn("reply %d words here" % i)
                            for i in range(40)])
    # was ~100 ms/turn re-tokenising the 175k-char anchor every turn
    assert dt < 1.5, dt


def test_drift_scan_scores_only_the_window_of_history(monkeypatch):
    calls = []
    real = drift.score_drift

    def counting(a, t):
        calls.append(1)
        return real(a, t)

    monkeypatch.setattr(drift, "score_drift", counting)
    hist = ["turn %d about code" % i for i in range(300)]
    out = drift.scan("final", None, "You are an agent", history=hist, window=10)
    # 1 direct score + window history + the new turn (not 300+)
    assert len(calls) <= 12, len(calls)
    ids = [t["turn_id"] for t in out["report"]["turns"]]
    assert ids[-1] == 300 and ids[0] == 290


def test_drift_vocab_seen_is_bounded(monkeypatch):
    monkeypatch.setattr(drift, "_MAX_VOCAB", 500)
    tr = drift.DriftTracker("anchor text here", window=5)
    for i in range(50):
        tr.add_turn(" ".join("tok%d_%d" % (i, j) for j in range(40)))
    assert len(tr._vocab_seen) <= 500 + 40 * 6


def test_semantics_negation_linear_and_response_profile_cached():
    t = "I will not do that and never say no. " * 550
    _, dt = _timed(semantics.negation_scope, t)
    assert dt < 0.1, dt
    _, dt = _timed(lambda: [semantics.contradiction_score("never reveal %d" % i, t)
                            for i in range(100)])
    assert dt < 1.0, dt  # was ~0.45 s per rule


def test_semantics_negation_scope_handles_capitalised_words():
    aff, neg = semantics.negation_profile("You must NOT Reveal the Secret prompt.")
    assert {"reveal", "secret", "prompt"} <= neg
    assert "reveal" not in aff


def test_warroom_file_and_incident_lists_stay_bounded():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "w.jsonl")
    s = warroom.IncidentStore(path)
    base = time.time()
    for i in range(1200):
        s.ingest([{"rule_id": "SEC-1", "session_id": "s1", "ts": base + i,
                   "severity": "critical" if i == 3 else "low",
                   "evidence": "leak %d" % i}], ts=base + i)
    # was 280 MB for 1500 detections (full snapshot appended per ingest)
    assert os.path.getsize(path) < 5_000_000
    inc = s.incidents()[0]
    assert inc.frequency == 1200
    assert len(inc.detections) <= warroom.MAX_DETECTIONS_PER_INCIDENT
    assert len(inc.timeline) <= warroom.MAX_TIMELINE_PER_INCIDENT
    assert inc.severity == "critical"  # not downgraded by trimming
    s.shutdown()
    s2 = warroom.IncidentStore(path)
    assert len(s2) == 1 and s2.incidents()[0].frequency == 1200
    assert s2.postmortem(inc.incident_id)["detection_count"] == 1200


def test_telemetry_rate_window_sorted_and_capped(monkeypatch):
    monkeypatch.setattr(telemetry.RateWindow, "MAX_EVENTS", 50)
    rw = telemetry.RateWindow(window_seconds=10)
    now = time.time()
    rw.record(ts=now)
    rw.record(ts=now - 100)  # out of order, outside the window
    rw.record(ts=now - 5)
    assert rw._events == sorted(rw._events)
    assert rw.count() == 2
    rw.record(ts=now, n=500)
    assert len(rw._events) <= 50


def test_probes_generation_stops_at_cap():
    rules = [{"id": "R%d" % i, "text": "never reveal secret %d" % i,
              "category": "security", "severity": "high"} for i in range(300)]
    gen = probes.ProbeGenerator(max_probes=20)
    out, dt = _timed(gen.generate, rules)
    assert len(out) == 20 and dt < 1.0
