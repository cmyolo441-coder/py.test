"""Regression tests for round-2 group-22 audit fixes."""
import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from types import SimpleNamespace  # noqa: E402

from fullagent import planmode  # noqa: E402
from fullagent._foundation import capture_logs_to_file, get_logger  # noqa: E402
from fullagent.enforcex import detect, forge  # noqa: E402
from fullagent.tools import RISK_CONFIRM  # noqa: E402


# ── detect ───────────────────────────────────────────────────────────────

def test_detect_check_secrets_flag_is_honoured():
    text = "key sk_abcdefghijklmnop123456"
    on = detect.DetectEngine().scan(text)
    off = detect.DetectEngine(detect.DetectConfig(check_secrets=False)).scan(text)
    assert any(d.rule_id.startswith("SEC-") for d in on)
    assert not any(d.rule_id.startswith("SEC-") for d in off)


def test_detect_policy_rule_stems_match_inflected_words():
    rules = [{"id": "R1", "text": "Never talk about politics or religion."}]
    vios = detect.scan("The election results were announced.", rules)
    pol = [v for v in vios if v["rule_id"] == "TOPIC-POLITICS"]
    assert pol and pol[0]["severity"] == "high"


def test_detect_aggregate_total_with_iterator():
    eng = detect.DetectEngine()
    dets = eng.scan("<system>x</system>")
    agg = eng.aggregate(iter(dets))
    assert agg["total"] == len(dets) == sum(agg["by_kind"].values())


# ── forge ────────────────────────────────────────────────────────────────

def test_forge_zero_priority_round_trips():
    pack, _ = forge.compile_pack([{"id": "R", "text": "x", "priority": 0}])
    assert pack["rules"][0]["priority"] == 0.0
    loaded, val = forge.load_pack(pack)
    assert val.ok and loaded["rules"][0]["priority"] == 0.0


def test_forge_infinite_int_policy_does_not_raise():
    pack, notes = forge.compile_pack([{"id": "R", "text": "x"}],
                                     policy={"max_retries": float("inf")})
    assert pack["policy"]["max_retries"] == 2
    assert any("max_retries" in n for n in notes)


def test_forge_non_list_patterns_or_keywords_never_raise():
    for key in ("patterns", "keywords"):
        pack, _ = forge.compile_pack([{"id": "R", "text": "x"}])
        pack["rules"][0][key] = 5
        pack["manifest"] = forge._build_manifest(pack, 0, 0)
        val = forge.validate_pack(pack)
        assert not val.ok and any(key in e for e in val.errors)
        loaded, val2 = forge.load_pack(pack)
        assert loaded is None and not val2.ok


# ── _foundation ──────────────────────────────────────────────────────────

def test_capture_logs_writes_each_record_once(tmp_path):
    log = get_logger("r2g22")
    path = tmp_path / "cap.log"
    with capture_logs_to_file(path):
        log.warning("only-once-marker")
    assert path.read_text(encoding="utf-8").count("only-once-marker") == 1


# ── planmode ─────────────────────────────────────────────────────────────

def test_revised_plan_requires_fresh_approval():
    agent = SimpleNamespace(tools={})
    planmode.register(agent)
    try:
        planmode.enter_plan_mode(agent)
        pw = agent.tools["PlanWrite"].handler
        pw(plan="1. read")
        planmode.approve_plan(agent)
        assert not planmode.should_block_mutating_tool(
            "Write", RISK_CONFIRM, agent)[0]
        pw(plan="1. delete everything")
        assert planmode.plan_requires_approval(agent)
        assert planmode.should_block_mutating_tool(
            "Write", RISK_CONFIRM, agent)[0]
    finally:
        planmode.exit_plan_mode(agent)
