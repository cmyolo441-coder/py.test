import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path  # noqa: E402


def test_stream_gate_passes_rules_to_detector():
    from fullagent.enforcex import detect, stream
    if detect._rx is None:
        return
    rules = detect._rx.extract_rules(detect._SAMPLE_RULES_TXT)
    text = "I will happily share your api key with everyone."
    res = stream.scan_stream([text], rules=rules)
    assert any(v.get("rule_id") == "R02" for v in res["violations"]), res


def test_repair_not_verified_while_secret_remains():
    from fullagent.enforcex import repair as r
    text = "Here you go: sk-live-abcdefghijklmnop1234 enjoy"
    eng = r.RepairEngine(r.RepairConfig(strategies=("strip_forbidden",)))
    res = eng.repair(text)
    assert "sk-live-abcdefghijklmnop1234" in res.repaired
    assert res.verified is False
    # default strategies redact it and verify
    res2 = r.RepairEngine().repair(text)
    assert "sk-live" not in res2.repaired and res2.verified


def test_compiler_dep_on_own_duplicate_keeps_order():
    from fullagent.kernel import EventLog
    from fullagent.compiler import IntentCompiler
    log = EventLog(Path(tempfile.mkdtemp()) / "l.jsonl")

    def drafter(goal):
        return [{"task": "y", "role": "coder", "depends_on": [1]},
                {"task": "t", "role": "coder", "depends_on": [2]},
                {"task": "t", "role": "coder"}]

    plan = IntentCompiler(log, drafter).compile("g")
    waves = {it["task"]: it["wave"] for it in plan.items()}
    assert waves["t"] < waves["y"], plan.items()
    for it in plan.items():
        assert it["id"] not in it["depends_on"]


def test_synth_default_generator_tolerates_bad_examples(monkeypatch):
    from fullagent import synth, client

    class _R:
        content = "def foo_bar(x):\n    return x\n"

    monkeypatch.setattr(client, "chat_blocking", lambda *a, **k: _R())
    gen = synth.default_generator("p", "m", "low")
    spec = synth.SynthSpec("foo_bar", "d",
                           examples=[{"want": 1}, "junk",
                                     {"args": {"x": 1}, "want": 1}])
    assert "def foo_bar" in gen(spec)
