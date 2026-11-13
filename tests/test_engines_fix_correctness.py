"""Regression tests for the engines-area audit (council, debate, mcts,
fuzz, mutate, synth, compiler, formal, tower, nexus, fabric, dual).

All offline and fast; app state is isolated under a temp FULLAGENT_HOME.
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="engines_"))

_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest  # noqa: E402

from fullagent.kernel import EventLog  # noqa: E402


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path / "events.jsonl")


# -- council -----------------------------------------------------------------

def test_council_winner_word_boundary():
    from fullagent.council import _parse_synthesis
    # "Argument B" used to match the A of "Argument" -> thesis won
    assert _parse_synthesis("WINNER: Argument B\nCONFIDENCE: 70%\n"
                            "REASON: x")[0] == "B"
    assert _parse_synthesis("WINNER: Both sides are weak")[0] == "DRAW"
    assert _parse_synthesis("WINNER: A\nCONFIDENCE: 82%")[:2] == ("A", 82.0)
    # a confidence without "%" is still a confidence
    assert _parse_synthesis("WINNER: B\nCONFIDENCE: 64")[1] == 64.0


# -- debate ------------------------------------------------------------------

def test_debate_single_link_merges_bridged_clusters(log):
    from fullagent.debate import DebateTournament

    # m1 ~ m3 and m2 ~ m3, but m1 !~ m2: true single-link puts all three
    # together. The old clustering left {m1, m3} | {m2}.
    answers = {"m1": "alpha beta gamma delta",
               "m2": "epsilon zeta eta theta",
               "m3": "alpha beta gamma delta epsilon zeta eta theta"}
    tour = DebateTournament(log, lambda m, p: answers[m], ["m1", "m2", "m3"])
    res = tour.run("q", rounds=1)
    assert len(res.clusters) == 1, res.clusters
    assert sorted(res.clusters[0]) == ["m1", "m2", "m3"]


# -- mcts --------------------------------------------------------------------

def test_mcts_bad_scores_do_not_crash(log):
    from fullagent.mcts import TreeSearch

    calls = {"n": 0}

    def evaluator(assignment):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            return None            # used to crash backprop (TypeError)
        if calls["n"] % 3 == 1:
            return float("nan")    # used to poison UCB1
        return 0.5

    r = TreeSearch(log, evaluator, seed=1).search(["a", "b"], ["x", "y"],
                                                  iterations=30)
    assert r.best_score == 0.5 and r.best_assignment


# -- fuzz --------------------------------------------------------------------

def test_fuzz_shrink_skips_noop_candidates(log):
    from fullagent.fuzz import Crash, Fuzzer

    def target(a, b):
        if isinstance(b, str) and len(b) > 3:
            raise ValueError("long")

    fz = Fuzzer(log, seed=1)
    c = Crash(args=(0.0, "abcdefghij"), error="ValueError: long")
    fz._shrink(c, target, 5.0)
    # the 0.0 arg's no-op "simpler" 0.0 used to win every step, so the
    # string was never shrunk
    assert c.shrunk_args[0] == 0.0
    assert len(c.shrunk_args[1]) == 4, c.shrunk_args


def test_fuzz_generator_depth_is_bounded():
    from fullagent.fuzz import Generator

    def depth(v):
        if isinstance(v, list):
            return 1 + max((depth(x) for x in v), default=0)
        if isinstance(v, dict):
            return 1 + max((depth(x) for x in v.values()), default=0)
        return 0

    g = Generator(seed=3)
    assert max(depth(g.any()) for _ in range(3000)) <= 3


def test_fuzz_hangs_are_capped(log, monkeypatch):
    from fullagent import fuzz

    monkeypatch.setattr(fuzz, "SHRINK_PROBE_TIMEOUT", 0.05)
    stop = threading.Event()

    def hang(x):
        while not stop.is_set():
            time.sleep(0.005)

    try:
        before = threading.active_count()
        t0 = time.monotonic()
        rep = fuzz.Fuzzer(log, seed=1).fuzz(hang, iterations=200,
                                            timeout=0.05)
        elapsed = time.monotonic() - t0
        leaked = threading.active_count() - before
    finally:
        stop.set()
    assert rep.iterations == fuzz.MAX_HANGS_PER_RUN
    assert rep.first_crash.kind == "hang"
    assert "no longer reproduces" not in rep.first_crash.shrunk_error
    assert elapsed < 5
    assert leaked <= fuzz.MAX_HANGS_PER_RUN * (1 + fuzz.MAX_HANG_PROBES)


# -- mutate ------------------------------------------------------------------

def _suite(tmp_path, body):
    suite = tmp_path / "test_calc.py"
    suite.write_text("import sys; sys.path.insert(0, %r)\n%s"
                     % (str(tmp_path), body))
    return f"{sys.executable} {suite}"


def test_mutate_restores_bytes_and_tests_fresh_bytecode(log, tmp_path):
    from fullagent.mutate import MutationTester

    src = (b"def f(a, b):\r\n    # caf\xe9\r\n    return a + b\r\n\r\n"
           b"def g(a, b):\r\n    return a - b\r\n")
    target = tmp_path / "calc.py"
    target.write_bytes(src)
    mtime = target.stat().st_mtime_ns
    cmd = _suite(tmp_path, "from calc import g\nassert g(5, 3) == 2\n")
    import subprocess
    subprocess.run(cmd.split(), check=True, stdin=subprocess.DEVNULL,
                   start_new_session=True)       # warm a .pyc
    rep = MutationTester(log, cmd, mutant_path=str(target)).run(str(target))
    # the two mutants of g are same-size twins of f's mutants; with a
    # stale .pyc both of g's mutants "survived"
    assert rep.killed == 2 and rep.survived == 2, rep.to_dict()
    # byte-exact restore: CRLF and latin-1 survive, and so does mtime
    assert target.read_bytes() == src
    assert target.stat().st_mtime_ns == mtime


def test_mutate_suite_timeout_kills_tree(log, tmp_path):
    from fullagent.mutate import MutationTester

    target = tmp_path / "calc.py"
    target.write_text("def f(a):\n    return a + 1\n")
    # the shell forks a long sleeper that holds the output pipe: the old
    # subprocess.run(timeout=) waited for it after killing the shell
    tester = MutationTester(log, "sleep 30 & sleep 30; wait",
                            mutant_path=str(target), timeout=1)
    t0 = time.monotonic()
    code, out = tester._run_suite()
    assert code == 124 and time.monotonic() - t0 < 10


def test_mutate_scratch_mutant_path_is_removed(log, tmp_path):
    from fullagent.mutate import MutationTester

    target = tmp_path / "calc.py"
    target.write_text("def f(a):\n    return a + 1\n")
    scratch = tmp_path / "scratch_calc.py"
    MutationTester(log, "true", mutant_path=str(scratch)).run(str(target))
    assert not scratch.exists()


# -- synth -------------------------------------------------------------------

def test_synth_blocks_frame_introspection_escape(log):
    from fullagent.synth import ProgramSynthesizer, SynthSpec

    src = ("def peek(x):\n"
           "    def gen():\n"
           "        yield g.gi_frame.f_back.f_back\n"
           "    g = gen()\n"
           "    fr = [v for v in g][0]\n"
           "    return 'threading' in fr.f_globals\n")
    s = ProgramSynthesizer(log, lambda spec: src, {})
    r = s.synthesize(SynthSpec("peek", "x",
                               [{"args": {"x": 1}, "want": True}]))
    assert not r.ok and "introspection" in r.reason, r


def test_synth_cannot_shadow_builtin_tool(log):
    from fullagent.synth import ProgramSynthesizer, SynthSpec

    sentinel = SimpleNamespace(description="Write a file")
    registry = {"write_file": sentinel}
    s = ProgramSynthesizer(
        log, lambda spec: f"def {spec.name}(path):\n    return path\n",
        registry)
    r = s.synthesize(SynthSpec("write_file", "x",
                               [{"args": {"path": "a"}, "want": "a"}]))
    assert not r.ok and registry["write_file"] is sentinel
    # re-synthesizing a tool we synthesized ourselves is still allowed
    spec = SynthSpec("echo_it", "x", [{"args": {"path": "a"}, "want": "a"}])
    assert s.synthesize(spec).ok and s.synthesize(spec).ok
    assert s.synthesized.count("echo_it") == 1


# -- compiler ----------------------------------------------------------------

def test_compiler_lockpass_keeps_dependency_order(log):
    from fullagent.compiler import IntentCompiler

    def drafter(goal):
        return [{"task": "w1", "role": "coder", "paths": ["a"]},
                {"task": "w2", "role": "coder", "paths": ["a"]},
                {"task": "w3", "role": "coder", "paths": ["a"]},
                {"task": "read w3", "role": "tester", "depends_on": [2]}]

    plan = IntentCompiler(log, drafter).compile("g")
    wave_of = {it["task"]: it["wave"] for it in plan.items()}
    # w3 is pushed to wave 2 by the lock pass; its dependent used to
    # stay in wave 1 and run BEFORE it
    assert wave_of["read w3"] > wave_of["w3"], plan.waves


def test_compiler_overflow_wave_is_dependency_ordered(log):
    from fullagent.compiler import IntentCompiler, _MAX_WAVES

    n = _MAX_WAVES + 4
    # a reverse chain: item i depends on item i+1
    def drafter(goal):
        return [{"task": f"t{i}", "role": "tester",
                 "depends_on": [i + 1] if i + 1 < n else []}
                for i in range(n)]

    plan = IntentCompiler(log, drafter).compile("g")
    wave_of = {it["task"]: it["wave"] for it in plan.items()}
    for i in range(n - 1):
        assert wave_of[f"t{i}"] > wave_of[f"t{i + 1}"], plan.waves


# -- formal ------------------------------------------------------------------

def test_formal_audits_any_iterable():
    from fullagent.formal import trace_from_events

    evs = [SimpleNamespace(type="tool.call", data={"name": "delete_path"})]
    assert trace_from_events(iter(evs)) == [{"delete"}]
    assert trace_from_events(None) == []


# -- tower -------------------------------------------------------------------

def _tower(log):
    from fullagent.tower import Tower
    turns = []
    agent = SimpleNamespace(log=log, brain=None,
                            run_turn=lambda text, **kw: turns.append(text))
    tower = Tower(agent)
    url = tower.start(port=0, open_browser=False)
    return tower, url, turns


def _post(url, body, headers):
    req = urllib.request.Request(url + "/api/command", data=body,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_tower_refuses_cross_site_commands(log):
    tower, url, turns = _tower(log)
    try:
        port = url.rsplit(":", 1)[1]
        body = json.dumps({"text": "rm everything"}).encode()
        # a foreign page's fetch(): text/plain needs no CORS preflight
        assert _post(url, body, {"Content-Type": "text/plain",
                                 "Origin": "https://evil.example"})[0] == 403
        assert _post(url, body, {"Content-Type": "application/json",
                                 "Origin": "https://evil.example"})[0] == 403
        # DNS rebinding: right IP, foreign Host name
        assert _post(url, body, {"Content-Type": "application/json",
                                 "Host": f"evil.example:{port}"})[0] == 403
        # the tower's own page is allowed
        code, out = _post(url, body, {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}"})
        assert code == 200 and out["ok"], out
        for _ in range(100):
            if turns:
                break
            time.sleep(0.02)
        assert turns == ["rm everything"]
    finally:
        tower.stop()


def test_tower_page_reads_events_envelope(log):
    from fullagent.tower import _PAGE
    # /api/events returns {"events": [...]}: iterating the envelope
    # threw inside pollEvents and the river never painted anything
    assert "(await r.json()).events" in _PAGE


# -- nexus -------------------------------------------------------------------

def test_nexus_same_name_defs_and_deleted_files(tmp_path):
    from fullagent.nexus import Nexus

    root = tmp_path / "build" / "proj"     # a repo living under "build"
    root.mkdir(parents=True)
    (root / "m.py").write_text(
        "class A:\n    def __init__(self):\n        pass\n\n"
        "class B:\n    def __init__(self):\n        pass\n")
    gone = root / "gone.py"
    gone.write_text("def doomed():\n    pass\n")
    nx = Nexus()
    nx.index(root)
    assert len(nx.find_symbol("__init__")) == 2
    assert nx.find_symbol("doomed")
    gone.unlink()
    nx.index(root)
    assert nx.find_symbol("doomed") == []
    # reindexing a changed file drops exactly its old symbols
    (root / "m.py").write_text("def only():\n    pass\n")
    nx.index(root)
    assert nx.find_symbol("__init__") == [] and nx.find_symbol("only")


# -- fabric ------------------------------------------------------------------

def test_fabric_reassert_does_not_duplicate(log):
    from fullagent.fabric import KnowledgeFabric

    fab = KnowledgeFabric(log)
    for _ in range(50):
        fab.assert_fact("api", "framework", "fastapi")
    assert len(fab.facts) == 1
    assert len(fab.query("api", "framework")) == 1
    with pytest.raises(ValueError):
        fab.assert_fact("api", "x", "y", valid_from="yesterday")


# -- dual --------------------------------------------------------------------

def test_dual_failing_fast_path_escalates(log):
    from fullagent.dual import DualProcess

    def fast(q):
        raise RuntimeError("model down")

    d = DualProcess(log, fast, lambda q: "deep answer")
    r = d.ask("what is up")
    assert r.system == 2 and r.answer == "deep answer"
    d2 = DualProcess(log, lambda q: None, lambda q: "deep")
    assert d2.ask("what is up").system == 2
