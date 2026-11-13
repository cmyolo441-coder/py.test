"""Regression: the token pre-check must judge INPUT size, not max_tokens.

Real v4.1.0 log: the very first message "hello" (~5.7k input tokens incl.
system prompt + tool schemas) to "ERNIE 5.5 Flash Preview" (kios, 128k
window) was refused with "conversation is too large ... (~5,692 input
tokens vs 128,000 context window)". Root cause: _ensure_window_fit
reserved the full requested max_tokens (MAX_TOKENS = 200_000) inside its
fit test, and 200k alone is larger than the 128k window, so nothing could
ever fit -> it pruned, still failed, and raised.

Required behaviour:
  * max_tokens is clamped to (window - input - margin) and the provider cap
  * pruning / refusal are triggered only by input size (+ a fixed minimum
    completion), never by the requested max_tokens
  * no pruning WARNING for a small conversation
  * genuinely oversized conversations still prune / raise as before

No network, no API keys: only build_payload() is exercised.
"""

import logging
import sys
from pathlib import Path as _Path

_REPO = str(_Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

import fullagent.client as c
from fullagent import config
from fullagent.config import Effort, Model

ERNIE_ID = "ernie-5.5-flash-preview"


# -- fixtures / helpers -----------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_calibration(monkeypatch):
    """No learned windows / corrections from disk; never write to disk."""
    monkeypatch.setattr(c, "_learned_windows", {})
    monkeypatch.setattr(c, "_correction_samples", {})
    monkeypatch.setattr(c, "_calibration_loaded", True)
    monkeypatch.setattr(c, "_save_calibration", lambda: None)
    c._bump_calibration_epoch()
    yield
    c._bump_calibration_epoch()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def client_log():
    logger = logging.getLogger("fullagent.client")
    h = _Capture()
    old = logger.level
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    yield h.records
    logger.removeHandler(h)
    logger.setLevel(old)


def _precheck_warnings(records):
    return [r for r in records
            if r.levelno >= logging.WARNING
            and ("pre-check" in r.getMessage()
                 or "pruning" in r.getMessage())]


def _ernie() -> Model:
    for m in config.MODELS:
        if m.id == ERNIE_ID:
            return m
    return Model(ERNIE_ID, "kios", "ERNIE 5.5 Flash Preview",
                 supports_tools=True,
                 context_window=config.MODEL_CONTEXT_WINDOWS.get(
                     ERNIE_ID, 128_000))


def _effort(max_tokens=config.MAX_TOKENS) -> Effort:
    return Effort(key="t", label="T", color="", max_tokens=max_tokens,
                  temperature=0.2, reasoning_effort=None, description="")


def _real_schemas() -> list[dict]:
    from fullagent.tools import build_registry
    return [t.openai_schema() for t in build_registry().values()]


def _many_schemas(n: int = 120) -> list[dict]:
    """The live agent sends ~100+ schemas; pad the real ones with renamed
    copies so the payload carries a realistic schema volume."""
    base = _real_schemas()
    out = list(base)
    i = 0
    while len(out) < n:
        s = base[i % len(base)]
        fn = dict(s["function"])
        fn["name"] = f"{fn['name']}_x{i}"
        out.append({**s, "function": fn})
        i += 1
    return out


def _hello_history(model: Model, tools, target: int = 5_700) -> list[dict]:
    """System prompt sized so system + "hello" + tool schemas ~= target."""
    tool_tokens = c.estimate_tokens(tools, model.id) if tools else 0
    sys_text = ""
    chunk = "You are FullAgent, a careful terminal coding agent. "
    while True:
        msgs = [{"role": "system", "content": sys_text},
                {"role": "user", "content": "hello"}]
        if c.estimate_tokens(msgs, model.id) + tool_tokens >= target:
            return msgs
        sys_text += chunk * 20


def _assert_clamped(model, payload, input_tokens):
    window = c.effective_window(model)
    mt = payload["max_tokens"]
    margin = c._context_margin(window) + input_tokens // 32
    assert mt >= c._MIN_COMPLETION_TOKENS
    assert mt <= window - input_tokens - margin
    assert input_tokens + mt <= window
    cap = c._MAX_TOKENS_CAP.get(model.provider)
    if cap is not None:
        assert mt <= cap


# -- the bug from the user's log --------------------------------------------

def test_hello_on_ernie_128k_with_200k_max_tokens_is_sent(client_log):
    model = _ernie()
    assert model.context_window == 128_000
    tools = _real_schemas()
    msgs = _hello_history(model, tools)
    n = c._input_tokens(model, msgs, tools)
    assert 5_000 <= n <= 8_000, n
    before = [dict(m) for m in msgs]

    payload = c.build_payload(model, _effort(200_000), msgs, tools,
                              stream=False)

    assert payload["messages"] == before          # nothing pruned
    assert not _precheck_warnings(client_log)     # no pruning WARNING
    _assert_clamped(model, payload, n)
    # A 128k window with ~6k input leaves well over 100k for the answer.
    assert payload["max_tokens"] > 100_000


@pytest.mark.parametrize("effort", config.EFFORTS, ids=lambda e: e.key)
def test_every_shipped_effort_fits_small_conversation(effort, client_log):
    model = _ernie()
    tools = _real_schemas()
    msgs = _hello_history(model, tools)
    n = c._input_tokens(model, msgs, tools)
    payload = c.build_payload(model, effort, msgs, tools, stream=False)
    assert not _precheck_warnings(client_log)
    assert len(payload["messages"]) == 2
    if effort.max_tokens:
        _assert_clamped(model, payload, n)


def test_bare_hello_without_tools(client_log):
    model = _ernie()
    msgs = [{"role": "user", "content": "hello"}]
    payload = c.build_payload(model, _effort(200_000), msgs, None,
                              stream=False)
    assert not _precheck_warnings(client_log)
    _assert_clamped(model, payload, c._input_tokens(model, msgs, None))


def test_hundred_plus_tool_schemas_are_counted_and_fit(client_log):
    model = _ernie()
    tools = _many_schemas(120)
    assert len(tools) >= 100
    msgs = _hello_history(model, None)
    n_no_tools = c._input_tokens(model, msgs, None)
    n = c._input_tokens(model, msgs, tools)
    assert n > n_no_tools                 # schemas are part of the input
    payload = c.build_payload(model, _effort(200_000), msgs, tools,
                              stream=False)
    assert not _precheck_warnings(client_log)
    assert payload["tools"] == tools
    _assert_clamped(model, payload, n)


# -- clamp properties -------------------------------------------------------

def test_provider_cap_applies_when_window_is_larger_than_cap():
    model = Model("big-kios", "kios", "Big", supports_tools=True,
                  context_window=1_000_000)
    msgs = [{"role": "user", "content": "hello"}]
    payload = c.build_payload(model, _effort(200_000), msgs, None,
                              stream=False)
    assert payload["max_tokens"] == c._MAX_TOKENS_CAP["kios"]


def test_max_tokens_larger_than_window_only_clamps(client_log):
    """Input that fits only if max_tokens is ignored: must NOT be pruned."""
    model = Model("mid", "nvidia", "Mid", supports_tools=False,
                  context_window=16_000)
    sys_msg = {"role": "system", "content": "sys"}
    msgs = [sys_msg] + [{"role": "user", "content": f"q{i} " + "word " * 120}
                        for i in range(30)]
    n = c._input_tokens(model, msgs, None)
    margin = c._context_margin(16_000) + n // 32
    assert n + margin + c._MIN_COMPLETION_TOKENS <= 16_000, n
    assert n + 200_000 > 16_000
    before = len(msgs)
    payload = c.build_payload(model, _effort(200_000), msgs, None,
                              stream=False)
    assert len(payload["messages"]) == before
    assert not _precheck_warnings(client_log)
    _assert_clamped(model, payload, n)


def test_learned_smaller_window_is_respected():
    model = _ernie()
    c.learn_context_window(model.id, 64_000)
    assert c.effective_window(model) == 64_000
    msgs = [{"role": "user", "content": "hello"}]
    payload = c.build_payload(model, _effort(200_000), msgs, None,
                              stream=False)
    _assert_clamped(model, payload, c._input_tokens(model, msgs, None))
    assert payload["max_tokens"] < 64_000


# -- genuinely oversized behaviour is unchanged -----------------------------

def test_oversized_history_still_prunes(client_log):
    model = Model("tiny", "kios", "Tiny", context_window=8_000)
    sys_msg = {"role": "system", "content": "sys"}
    msgs = [sys_msg] + [{"role": "user", "content": f"q{i} " + "x" * 500}
                        for i in range(90)]
    msgs.append({"role": "user", "content": "LATEST"})
    payload = c.build_payload(model, _effort(1_000), msgs, None,
                              stream=False)
    assert len(payload["messages"]) < 92
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1]["content"] == "LATEST"
    assert _precheck_warnings(client_log)
    n = c._input_tokens(model, payload["messages"], None)
    assert n + payload["max_tokens"] <= 8_000


def test_input_alone_too_big_still_raises():
    model = Model("tiny", "kios", "Tiny", context_window=8_000)
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "LATEST " + "z" * 60_000}]
    with pytest.raises(c.APIError) as ei:
        c.build_payload(model, _effort(200_000), msgs, None, stream=False)
    assert c.is_context_overflow(str(ei.value))
