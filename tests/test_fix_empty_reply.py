"""Bug: Gemini 4 Argon sent back an empty reply (no text, no tool call)
and the turn ended silently — "✦ done" with nothing on screen — while an
empty assistant message went into history. Now the agent retries once
and, if the reply is still empty, reports a clear error. No network."""
import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="emptyrep_"))

import pytest  # noqa: E402

import fullagent.agent as agent_mod  # noqa: E402
from fullagent.client import StreamResult  # noqa: E402

from test_fix_model_switch import _run, agent, home, no_network  # noqa: E402,F401,E501


def _text(content):
    return StreamResult(content=content, tool_calls=[], usage=None,
                        model="t")


def test_empty_reply_is_retried_once(agent, monkeypatch):
    replies = iter(["", "Hello there!"])
    calls = []

    def chat(*_a, **_k):
        calls.append(1)
        return _text(next(replies))

    monkeypatch.setattr(agent_mod, "chat_stream", chat)
    turn = _run(agent, "hello")
    assert not turn.error, turn.error
    assert turn.assistant_text == "Hello there!"
    assert len(calls) == 2


def test_persistent_empty_reply_is_an_error_not_done(agent, monkeypatch):
    calls = []

    def chat(*_a, **_k):
        calls.append(1)
        return _text("  \n")

    monkeypatch.setattr(agent_mod, "chat_stream", chat)
    turn = _run(agent, "make a web scanner")
    assert "empty reply" in turn.error
    assert len(calls) == 2
    # nothing empty (and no dangling user prompt) left in history
    assert not any(m.get("role") == "assistant"
                   and not (m.get("content") or "").strip()
                   for m in agent.messages)
    assert not any(m.get("role") == "user"
                   and m.get("content") == "make a web scanner"
                   for m in agent.messages)


@pytest.mark.parametrize("reply", ["ok"])
def test_normal_reply_untouched(agent, monkeypatch, reply):
    monkeypatch.setattr(agent_mod, "chat_stream",
                        lambda *_a, **_k: _text(reply))
    turn = _run(agent, "hi")
    assert not turn.error and turn.assistant_text == reply
