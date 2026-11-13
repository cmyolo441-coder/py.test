"""Regression: a turn whose model call produces nothing must never end as a
silent "✦ done".

Real incident (Gemini 4 Argon, gwarden):

    ❯ oo suabgwnts ko parllel me run karo ...
    ✦ done
    1.2s

The model returned no text and no tool calls; the agent appended an empty
assistant message and reported success. Now an empty reply is retried once
on the SAME model with the SAME messages, and a second empty reply ends the
turn with an error that names the model (and the finish reason).
"""

import dataclasses
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="empty_reply_home_"))
_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

import fullagent.agent as agent_mod
from fullagent.client import (StreamResult, TurnCancelled, chat_blocking,
                              chat_stream, validate_history)
from fullagent.config import Config

MODEL_ID = "gemini-4-argon"
MODEL_LABEL = "Gemini 4 Argon"


@pytest.fixture()
def agent(monkeypatch, tmp_path):
    import fullagent.config as config_mod
    monkeypatch.setattr(config_mod, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config_mod, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config_mod, "SESSIONS_DIR",
                        tmp_path / "app" / "sessions")
    monkeypatch.setattr(config_mod, "CONFIG_FILE",
                        tmp_path / "app" / "config.json")
    config_mod.ensure_dirs()
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    cfg.model_id = MODEL_ID
    ag = agent_mod.Agent(cfg)
    ag.autonomy = 5
    ag._ensure_session()
    yield ag
    try:
        ag.subagents.shutdown(wait=False)
    except Exception:
        pass


class FakeChat:
    """Scripted chat_stream: each call pops the next StreamResult (or
    callable) and records the model + the messages it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, provider, model, effort, messages, schemas, **kw):
        self.calls.append({"model": model.id,
                           "messages": json.dumps(messages, sort_keys=True,
                                                  default=str)})
        step = self.script.pop(0)
        if callable(step):
            return step(kw)
        if step.content and kw.get("on_token"):
            kw["on_token"](step.content)
        if step.reasoning and kw.get("on_reasoning"):
            kw["on_reasoning"](step.reasoning)
        return step


def _empty(finish="stop", reasoning=""):
    return StreamResult(content="", reasoning=reasoning, tool_calls=[],
                        finish_reason=finish, usage=None, model=MODEL_ID)


def _text(text):
    return StreamResult(content=text, tool_calls=[], finish_reason="stop",
                        usage=None, model=MODEL_ID)


def _tool_call(path):
    return StreamResult(content="", tool_calls=[{
        "id": "call_1", "type": "function",
        "function": {"name": "read_file",
                     "arguments": json.dumps({"path": str(path)})}}],
        finish_reason="tool_calls", usage=None, model=MODEL_ID)


def _run(ag, text, statuses=None, should_cancel=None):
    statuses = statuses if statuses is not None else []
    return ag.run_turn(text, lambda s: None, lambda s: None,
                       lambda e: None, lambda e: None, statuses.append,
                       lambda t, a: True,
                       should_cancel=should_cancel or (lambda: False))


def _assert_history_valid(ag):
    errors = [d for d in validate_history(ag.messages)
              if d.severity == "error"]
    assert not errors, errors
    for m in ag.messages:
        if m.get("role") == "assistant" and not m.get("tool_calls"):
            assert str(m.get("content") or "").strip(), \
                "an empty assistant message reached history"


PROMPT = ("oo suabgwnts ko parllel me run karo aur ek abhut he advanced "
          "level ka freamwork devlop akro wbe scanning ka")


def test_empty_then_text_retries_once_on_same_model(agent, monkeypatch):
    fake = FakeChat([_empty(), _text("Here is the plan.")])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    statuses = []
    turn = _run(agent, PROMPT, statuses)
    assert not turn.error, turn.error
    assert turn.assistant_text == "Here is the plan."
    assert len(fake.calls) == 2                     # exactly one extra call
    assert {c["model"] for c in fake.calls} == {MODEL_ID}   # never switched
    assert fake.calls[0]["messages"] == fake.calls[1]["messages"]
    assert any("empty reply" in s and "asking again" in s
               for s in statuses), statuses
    assert agent.messages[-1] == {"role": "assistant",
                                  "content": "Here is the plan."}
    _assert_history_valid(agent)


def test_empty_twice_ends_with_error_and_clean_history(agent, monkeypatch):
    fake = FakeChat([_empty("stop"), _empty("stop")])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    n_before = len(agent.messages)
    turn = _run(agent, PROMPT)
    assert len(fake.calls) == 2
    assert {c["model"] for c in fake.calls} == {MODEL_ID}
    assert turn.error, "an empty turn must never end as a silent done"
    assert MODEL_LABEL in turn.error
    assert "empty reply twice" in turn.error
    assert "finish reason: stop" in turn.error
    assert "Ctrl+T" in turn.error
    assert turn.assistant_text == ""
    # the user message stays; no empty assistant message is appended
    assert agent.messages[-1]["role"] == "user"
    assert agent.messages[-1]["content"] == PROMPT
    assert len(agent.messages) >= n_before + 1
    _assert_history_valid(agent)
    # the session stays usable: the next turn works normally
    monkeypatch.setattr(agent_mod, "chat_stream", FakeChat([_text("ok")]))
    nxt = _run(agent, "again")
    assert not nxt.error and nxt.assistant_text == "ok"
    _assert_history_valid(agent)


def test_unknown_finish_reason_is_left_out(agent, monkeypatch):
    monkeypatch.setattr(agent_mod, "chat_stream",
                        FakeChat([_empty(None), _empty(None)]))
    turn = _run(agent, PROMPT)
    assert MODEL_LABEL in turn.error
    assert "finish reason" not in turn.error


def test_whitespace_only_reply_counts_as_empty(agent, monkeypatch):
    fake = FakeChat([_text("  \n "), _text("real answer")])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, PROMPT)
    assert not turn.error
    assert len(fake.calls) == 2
    assert turn.assistant_text == "real answer"


def test_reasoning_only_counts_as_empty_but_keeps_reasoning(agent,
                                                            monkeypatch):
    fake = FakeChat([_empty("stop", reasoning="first thoughts"),
                     _empty("length", reasoning="second thoughts")])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, PROMPT)
    assert len(fake.calls) == 2
    assert turn.error and MODEL_LABEL in turn.error
    assert "finish reason: length" in turn.error
    assert "first thoughts" in turn.reasoning
    assert "second thoughts" in turn.reasoning
    assert agent.messages[-1]["role"] == "user"
    _assert_history_valid(agent)


def test_reasoning_only_then_text_succeeds(agent, monkeypatch):
    fake = FakeChat([_empty("stop", reasoning="hmm"), _text("answer")])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, PROMPT)
    assert not turn.error
    assert turn.assistant_text == "answer"
    assert "hmm" in turn.reasoning


def test_empty_after_tool_step_retries_then_errors(agent, monkeypatch,
                                                   tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello from disk\n")
    fake = FakeChat([_tool_call(target), _empty(), _empty()])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, "read notes.txt")
    assert len(fake.calls) == 3
    assert fake.calls[1]["messages"] == fake.calls[2]["messages"]
    assert [t.name for t in turn.tools] == ["read_file"]
    assert turn.error and "empty reply twice" in turn.error
    # tool pairing intact, no empty assistant message, last is the tool row
    assert agent.messages[-1]["role"] == "tool"
    assert agent.messages[-1]["tool_call_id"] == "call_1"
    _assert_history_valid(agent)


def test_empty_after_tool_step_retry_recovers(agent, monkeypatch, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello from disk\n")
    fake = FakeChat([_tool_call(target), _empty(), _text("It says hello.")])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, "read notes.txt")
    assert not turn.error, turn.error
    assert len(fake.calls) == 3
    assert turn.assistant_text == "It says hello."
    _assert_history_valid(agent)


def test_cancel_during_retry(agent, monkeypatch):
    cancel = {"on": False}

    def retry_gets_cancelled(kw):
        # the user presses Esc while the retry is streaming: the real
        # chat_stream notices should_cancel and raises TurnCancelled
        cancel["on"] = True
        if kw["should_cancel"]():
            raise TurnCancelled()
        return _text("should not get here")

    fake = FakeChat([_empty(), retry_gets_cancelled])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, PROMPT, should_cancel=lambda: cancel["on"])
    assert turn.error == "cancelled"
    assert len(fake.calls) == 2              # no third call after cancel
    _assert_history_valid(agent)


def test_cancel_before_retry_skips_it(agent, monkeypatch):
    cancel = {"on": False}

    def empty_then_esc(kw):
        cancel["on"] = True                  # Esc lands after the reply
        return _empty()

    fake = FakeChat([empty_then_esc])
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    turn = _run(agent, PROMPT, should_cancel=lambda: cancel["on"])
    assert turn.error == "cancelled"
    assert len(fake.calls) == 1              # the retry never went out
    _assert_history_valid(agent)


# --------------------------------------------------------------------------
# the real client against a tiny local HTTP server
# --------------------------------------------------------------------------

_BODIES = {
    "done_only": ("text/event-stream", b"data: [DONE]\n\n"),
    "usage_only": ("text/event-stream",
                   b'data: {"choices": [], "usage": {"prompt_tokens": 5, '
                   b'"completion_tokens": 0}}\n\ndata: [DONE]\n\n'),
    "stop_only": ("text/event-stream",
                  b'data: {"choices": [{"index": 0, "delta": {}, '
                  b'"finish_reason": "stop"}]}\n\ndata: [DONE]\n\n'),
    "empty_sse": ("text/event-stream", b""),
    "empty_json": ("application/json", b""),
}


class _Server:
    def __init__(self, mode):
        self.mode = mode
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                try:
                    outer.requests.append(json.loads(body or b"{}"))
                except ValueError:
                    outer.requests.append({})
                ctype, payload = _BODIES[outer.mode]
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self):
        return "http://%s:%d" % self.httpd.server_address


def _local_provider(base_url):
    from fullagent.config import PROVIDERS, model_by_id
    model = model_by_id(MODEL_ID)
    # a throwaway key: the embedded provider key is never used or sent
    prov = dataclasses.replace(PROVIDERS[model.provider], base_url=base_url,
                               api_key="test-key-not-real")
    return prov, model


@pytest.fixture()
def no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.mark.parametrize("mode", ["done_only", "usage_only", "stop_only",
                                  "empty_sse", "empty_json"])
def test_real_client_empty_stream_is_empty_result(mode, no_proxy):
    from fullagent.config import effort_by_key
    with _Server(mode) as srv:
        prov, model = _local_provider(srv.base_url)
        res = chat_stream(prov, model, effort_by_key("low"),
                          [{"role": "user", "content": "hi"}], None,
                          timeout=10)
    assert isinstance(res, StreamResult)
    assert res.content == "" and res.tool_calls == []
    assert agent_mod._is_empty_reply(res)
    if mode == "usage_only":
        assert res.usage and res.usage.get("prompt_tokens") == 5
    if mode == "stop_only":
        assert res.finish_reason == "stop"
    assert len(srv.requests) == 1


def test_real_blocking_client_empty_body_is_empty_result(no_proxy):
    from fullagent.config import effort_by_key
    with _Server("empty_json") as srv:
        prov, model = _local_provider(srv.base_url)
        res = chat_blocking(prov, model, effort_by_key("low"),
                            [{"role": "user", "content": "hi"}], None,
                            timeout=10)
    assert res.content == "" and res.tool_calls == []


def test_agent_against_real_client_done_only(agent, monkeypatch, no_proxy):
    """End to end: the real chat_stream talking to a server that answers
    every request with only `data: [DONE]` — the turn retries once and then
    ends with the explanatory error, never a silent done."""
    import fullagent.config as config_mod
    with _Server("done_only") as srv:
        prov, model = _local_provider(srv.base_url)
        monkeypatch.setitem(config_mod.PROVIDERS, model.provider, prov)
        statuses = []
        turn = _run(agent, PROMPT, statuses)
    assert len(srv.requests) == 2
    sent_models = {r.get("model") for r in srv.requests}
    assert len(sent_models) == 1                    # same model both times
    assert MODEL_ID in str(sent_models.pop())
    assert turn.error and MODEL_LABEL in turn.error
    assert "empty reply twice" in turn.error
    assert any("asking again" in s for s in statuses)
    assert agent.messages[-1]["role"] == "user"
    _assert_history_valid(agent)
