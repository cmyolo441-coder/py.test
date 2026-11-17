import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import attest, continueresume  # noqa: E402


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






