"""Long-session performance regression tests (progressive slowdown).

Symptom: the longer the agent worked, the slower every turn got, until it
stopped responding even though the API answered normally. Root causes
fixed (each has a test below):

  1. Token-count caches were keyed on a calibration epoch that was bumped
     after EVERY model response, so every response threw every cached
     count away and the next estimate re-tokenized the whole history in a
     pure-Python per-character loop (several times per tool iteration).
  2. The canonical history grew forever although only the last
     prune_window messages ever reach the wire, so every per-iteration pass
     over it (checkpoints, estimates, turn-resume dumps) grew too.
  3. Auto-checkpoints deep-copied the WHOLE history before every risky
     tool call.
  4. Agent._run_tool_with_timeout swapped the global subprocess.Popen per
     call; parallel calls left an ever-growing wrapper chain installed.

All tests are offline and drive the real Agent with a fake chat_stream.
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

_tmp_home = tempfile.mkdtemp(prefix="perf_home_")
os.environ.setdefault("FULLAGENT_HOME", _tmp_home)

_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest

import fullagent.agent as agent_mod
from fullagent import client
from fullagent.checkpoints import CheckpointManager
from fullagent.client import StreamResult
from fullagent.config import Config


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

class FakeAPI:
    """Fake chat_stream: a few tool rounds per turn, then a text reply.

    Every call also feeds the token calibrator, like a real response with
    usage does — that is what used to wipe the token caches."""

    def __init__(self):
        self.k = 0
        self.rounds_left = 0

    def __call__(self, provider, model, effort, messages, schemas, **kw):
        self.k += 1
        client.learn_token_correction(1000.0, 1000 + (self.k % 7) * 10,
                                      getattr(model, "id", ""))
        usage = {"prompt_tokens": 3000, "completion_tokens": 100,
                 "total_tokens": 3100}
        if self.rounds_left > 0:
            self.rounds_left -= 1
            i = self.k
            tcs = [
                {"id": f"c{i}a", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": json.dumps({"path": f"f{i % 5}.py"})}},
                {"id": f"c{i}b", "type": "function",
                 "function": {"name": "write_file",
                              "arguments": json.dumps(
                                  {"path": f"out{i % 3}.txt",
                                   "content": ("line %d\n" % i) * 200})}},
            ]
            return StreamResult(content="working", tool_calls=tcs,
                                usage=usage, model="t")
        return StreamResult(content="Done. " * 200, tool_calls=[],
                            usage=usage, model="t")


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
    work = tmp_path / "work"
    work.mkdir()
    for i in range(5):
        (work / f"f{i}.py").write_text("def f():\n    return %d\n" % i * 40)
    monkeypatch.chdir(work)
    ag = agent_mod.Agent(Config())
    ag.autonomy = 5
    fake = FakeAPI()
    monkeypatch.setattr(agent_mod, "chat_stream", fake)
    ag._fake = fake
    return ag


def _turn(ag, text):
    ag._fake.rounds_left = 3
    return ag.run_turn(text, lambda s: None, lambda s: None,
                       lambda e: None, lambda e: None, lambda s: None,
                       lambda t, a: True, should_cancel=lambda: False)


# --------------------------------------------------------------------------
# 1. end to end: per-turn time stays flat, history stays bounded
# --------------------------------------------------------------------------

def test_turn_time_stays_flat_over_many_turns(agent):
    times = []
    for t in range(110):
        s = time.perf_counter()
        turn = _turn(agent, f"edit f{t % 5}.py and report {t}")
        times.append(time.perf_counter() - s)
        assert not turn.error, turn.error
    early = statistics.median(times[5:20])
    late = statistics.median(times[-15:])
    # Pre-fix this ratio was ~3x after 60 turns and kept climbing.
    assert late < early * 2.0 + 0.05, (early, late)
    cap, _ = agent._history_cap()
    assert len(agent.messages) <= cap
    # the sealed system prompt and the task framing survive the bound
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[1]["role"] == "user"
    assert "edit f0.py and report 0" in agent.messages[1]["content"]


def test_history_bound_keeps_tool_pairing(agent):
    for t in range(40):
        _turn(agent, f"task {t}")
    ids = [tc["id"] for m in agent.messages if m.get("role") == "assistant"
           for tc in (m.get("tool_calls") or [])]
    answered = {m.get("tool_call_id") for m in agent.messages
                if m.get("role") == "tool"}
    assert ids and all(i in answered for i in ids)
    # no orphan tool message without its assistant call
    assert answered <= set(ids)


# --------------------------------------------------------------------------
# 2. token estimation: calibration changes no longer force a recount
# --------------------------------------------------------------------------

def test_token_cache_survives_calibration_updates(monkeypatch):
    line = "def foo(bar):\n    return bar + 1  # some python code\n"
    hist = [{"role": "user", "content": line * 400 + str(i)}
            for i in range(30)]
    client.estimate_tokens(hist, "perf-model")
    calls = {"n": 0}
    real = client._heuristic_raw

    def counting(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(client, "_heuristic_raw", counting)
    monkeypatch.setattr(client, "_tiktoken_count", lambda text: None)
    before = client.estimate_tokens(hist, "perf-model")
    client.learn_token_correction(1000.0, 500, "perf-model")
    hist.append({"role": "assistant", "content": "new message"})
    after = client.estimate_tokens(hist, "perf-model")
    # only the ONE new message was tokenized, not the whole history
    assert calls["n"] == 1
    # and the new calibration is applied on read
    assert after < before


def test_estimate_tokens_fast_is_content_keyed():
    a = "x" * 1000
    b = "the quick brown fox jumps " * 40
    assert client.estimate_tokens_fast(a) != client.estimate_tokens_fast(b)
    # same length, different content: must not share a cache entry
    c = "y" * 999 + "z"
    d = "Q1Z9" * 250
    assert client.estimate_tokens_fast(c) != client.estimate_tokens_fast(d)


def test_heuristic_matches_reference_formula():
    import random
    random.seed(7)
    alpha = "abcXYZ0123 \n\t{}\":,.'中文テスト한국어_-/"

    def ref(text):
        if not text:
            return 0
        words = client._WORD_RE.findall(text)
        wc = 0
        wt = 0.0
        for w in words:
            wc += len(w)
            wt += client._word_tokens_uncached(w)
        cjk = sum(1 for ch in text if client._is_cjk(ch))
        sp = sum(1 for ch in text if not client._is_cjk(ch)
                 and ch.isspace())
        punct = len(text) - wc - cjk - sp
        return max(1, int(wt + cjk + punct / 2.5 + sp / 8.0))

    for _ in range(500):
        t = "".join(random.choice(alpha)
                    for _ in range(random.randint(0, 200)))
        assert client._heuristic_token_count(t) == ref(t)


# --------------------------------------------------------------------------
# 3. checkpoints share unchanged messages instead of deep-copying all
# --------------------------------------------------------------------------

def test_checkpoints_share_unchanged_messages():
    mgr = CheckpointManager(session_id="perf-test")
    ag = SimpleNamespace(messages=[{"role": "system", "content": "s"},
                                   {"role": "user", "content": "u"}])
    c1 = mgr.create(ag, "one")
    ag.messages.append({"role": "assistant", "content": "a"})
    c2 = mgr.create(ag, "two")
    s1 = mgr._snapshots[c1]["messages"]
    s2 = mgr._snapshots[c2]["messages"]
    assert s1[0] is s2[0] and s1[1] is s2[1]      # shared, not re-copied
    assert len(s1) == 2 and len(s2) == 3
    # an in-place edit is detected and re-copied; old snapshot unchanged
    ag.messages[1]["content"] = "u edited"
    c3 = mgr.create(ag, "three")
    assert mgr._snapshots[c3]["messages"][1]["content"] == "u edited"
    assert s2[1]["content"] == "u"
    # restore hands back independent copies
    mgr.restore(ag, c1)
    ag.messages[0]["content"] = "mutated"
    assert mgr._snapshots[c1]["messages"][0]["content"] == "s"


# --------------------------------------------------------------------------
# 4. parallel tool runs leave subprocess.Popen as it was
# --------------------------------------------------------------------------

def test_parallel_tool_runs_restore_popen():
    orig = subprocess.Popen
    ag = agent_mod.Agent.__new__(agent_mod.Agent)

    def h1(**k):
        time.sleep(0.02)
        subprocess.run(["true"])
        time.sleep(0.05)

    def h2(**k):
        time.sleep(0.04)
        subprocess.run(["true"])
        time.sleep(0.08)

    for _ in range(5):
        ts = [threading.Thread(target=ag._run_tool_with_timeout,
                               args=(SimpleNamespace(name=n, handler=h),
                                     {}, {}))
              for n, h in (("a", h1), ("b", h2))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert subprocess.Popen is orig
    assert isinstance(object(), subprocess.Popen) is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_tool_timeout_kills_spawned_child(monkeypatch):
    ag = agent_mod.Agent.__new__(agent_mod.Agent)
    monkeypatch.setitem(agent_mod.TOOL_TIMEOUTS, "slowtool", 0.3)
    box = {}

    def slow(**k):
        box["p"] = subprocess.Popen(["sleep", "30"], start_new_session=True)
        box["p"].wait()

    with pytest.raises(agent_mod.ToolTimeout):
        ag._run_tool_with_timeout(SimpleNamespace(name="slowtool",
                                                  handler=slow), {}, {})
    time.sleep(0.2)
    assert box["p"].poll() is not None
