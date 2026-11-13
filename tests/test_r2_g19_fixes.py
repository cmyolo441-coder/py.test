import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import attest, continueresume  # noqa: E402
from fullagent.enforcex import ruleextract as rx  # noqa: E402
from fullagent.enforcex import rulestore as rs  # noqa: E402
from fullagent.enforcex.orchestrator import EnforcementOutcome  # noqa: E402
from fullagent.enforcex.scoring import DriftTracker, Score  # noqa: E402


def _agent(user_text, prev_turns):
    return SimpleNamespace(
        messages=[{"role": "system", "content": "SYS"},
                  {"role": "user", "content": user_text}],
        turns=prev_turns, log=None)


def test_second_capped_continue_keeps_original_goal():
    cap = "Turn stopped after 25 tool calls — ask me to continue."
    t1 = SimpleNamespace(user_text="Refactor payments to Stripe",
                         assistant_text="", tools=[], error=cap)
    t2 = SimpleNamespace(user_text="continue", assistant_text="",
                         tools=[], error=cap)
    agent = _agent("continue", [t1, t2])
    assert continueresume.seed_continue_history(agent, "continue")
    assert agent.messages[1]["content"] == "Refactor payments to Stripe"
    assert "User goal: Refactor payments to Stripe" in agent.messages[2]["content"]


def test_envelope_with_bracket_doc_id_opens():
    sealed = attest.seal_envelope("hello world", doc_id="doc[1] v2")
    body, rep = attest.open_envelope(sealed)
    assert rep.ok, rep.reason
    assert body == "hello world"


def test_rulestore_latest_after_history_trim(tmp_path):
    store = rs.RuleStore(str(tmp_path / "r.json"))
    a = rx.build_spec("Always answer in Hindi.")
    b = rx.build_spec("Never use bullet lists.")
    first, second = sorted([a, b], key=lambda s: s.signature, reverse=True)
    store.save_spec(first)
    store.save_spec(second)
    for _ in range(rs.HISTORY_LIMIT):
        store.append_history({"event": "tick"})
    assert store.latest().signature == second.signature
    # re-saving the older spec makes it the newest
    store.save_spec(first)
    for _ in range(rs.HISTORY_LIMIT):
        store.append_history({"event": "tick"})
    assert store.latest().signature == first.signature


def test_scoring_from_dict_inf_never_raises():
    assert Score.from_dict({"violations_count": float("inf")}).violations_count == 0
    assert DriftTracker.from_dict({"window": float("inf")}).to_dict()["window"] == 100


def test_outcome_from_dict_malformed_never_raises():
    out = EnforcementOutcome.from_dict({
        "ledger_seq": None,
        "stages": [{"name": "detect", "ok": True, "extra": 1,
                    "elapsed_ms": "x"}]})
    assert out.ledger_seq == -1
    assert out.stages[0].name == "detect" and out.stages[0].ok
    assert out.stages[0].elapsed_ms == 0.0
