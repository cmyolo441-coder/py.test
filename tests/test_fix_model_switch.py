"""The main agent must run every turn on the model the user chose.

Real-user report (v4.1.0): the header said "DeepSeek V4.1 Flash 1017", yet
the first turn ran on "ERNIE 5.5 Flash Preview" and a later one on
"Gemini 4 Argon" with no visible model change. No router / adapt /
fallback writes ``cfg.model_id``; the switch came from the Ctrl+T model
picker:

* the picker stayed open while the user typed at the prompt, so the arrow
  keys (history recall) moved the picker and Enter committed the highlighted
  model instead of sending the message;
* the commit was silent: a transient flash only, no transcript line and no
  event-log record;
* a change made mid-turn took effect on the very next request of the
  RUNNING turn (``_complete`` re-read ``cfg.model_id`` on every call), while
  the turn recorded the old model; ``/model <q> --once`` recorded the
  override but sent the request to the base model.

All fakes: no network, no API key.
"""
import asyncio
import io
import json
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp(prefix="modelsw_"))

import pytest  # noqa: E402

import fullagent.agent as agent_mod  # noqa: E402
from fullagent import config  # noqa: E402
from fullagent.client import StreamResult  # noqa: E402
from fullagent.config import DEFAULT_MODEL_ID, Config  # noqa: E402

ERNIE = "ernie-5.5-flash-preview"
GEMINI = "gemini-4-argon"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No warm-up request and no HTTP client: every model call is faked."""
    from fullagent import client

    def _no_http(*_a, **_k):
        raise AssertionError("network access in a model-switch test")

    monkeypatch.setattr(client, "prewarm_connection", lambda _p: None)
    monkeypatch.setattr(client, "_http", _no_http)


@pytest.fixture()
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    monkeypatch.setattr(config, "EVENT_LOG_FILE",
                        tmp_path / "app" / "eventlog.jsonl")
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "app" / "sessions")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "app" / "config.json")
    config.ensure_dirs()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def agent(home):
    agent_mod._resolve_lazy(*agent_mod._LAZY_IMPORTS)
    ag = agent_mod.Agent(Config())
    ag._ensure_session()
    yield ag
    try:
        ag.subagents.shutdown(wait=False)
    except Exception:
        pass


def _run(ag, text):
    return ag.run_turn(text, lambda s: None, lambda s: None,
                       lambda e: None, lambda e: None, lambda s: None,
                       lambda t, a: True, should_cancel=lambda: False)


def _text(content):
    return StreamResult(content=content, tool_calls=[], usage=None,
                        model="t")


# --------------------------------------------------------------------------
# agent: one model per turn, the one that is reported
# --------------------------------------------------------------------------

def test_turn_uses_selected_model_and_never_changes_it(agent, monkeypatch):
    sent = []

    def chat(provider, model, *_a, **_k):
        sent.append((provider.key, model.id))
        return _text("hi")

    monkeypatch.setattr(agent_mod, "chat_stream", chat)
    for text in ("hello", "hii", "write a python web scanner"):
        turn = _run(agent, text)
        assert not turn.error, turn.error
        assert turn.model_id == DEFAULT_MODEL_ID
    assert sent and set(sent) == {("gwarden", DEFAULT_MODEL_ID)}
    assert agent.cfg.model_id == DEFAULT_MODEL_ID
    assert agent.model.id == DEFAULT_MODEL_ID


def test_model_picked_mid_turn_does_not_switch_the_running_turn(
        agent, monkeypatch):
    """A Ctrl+T pick while a turn runs applies from the NEXT turn; the
    running turn keeps one model for every request."""
    sent = []

    def chat(provider, model, *_a, **_k):
        sent.append(model.id)
        if len(sent) == 1:
            agent.cfg.model_id = GEMINI          # the picker, mid-turn
            return StreamResult(content="", tool_calls=[{
                "id": "c1", "type": "function",
                "function": {"name": "list_dir",
                             "arguments": json.dumps({"path": "."})}}],
                usage=None, model="t")
        return _text("done")

    monkeypatch.setattr(agent_mod, "chat_stream", chat)
    turn = _run(agent, "hello")
    assert not turn.error, turn.error
    assert len(sent) >= 2
    assert sent == [DEFAULT_MODEL_ID] * len(sent), sent
    assert turn.model_id == DEFAULT_MODEL_ID
    # the pick is honoured from the next turn on
    sent.clear()
    turn2 = _run(agent, "hii")
    assert sent and set(sent) == {GEMINI}
    assert turn2.model_id == GEMINI


def test_once_override_is_the_model_actually_sent_and_shown(
        agent, monkeypatch):
    from fullagent.modelpick import set_turn_model
    sent, shown = [], []

    def chat(provider, model, *_a, **_k):
        sent.append((provider.key, model.id))
        shown.append(agent.model.id)     # what the status bar reads mid-turn
        return _text("hi")

    monkeypatch.setattr(agent_mod, "chat_stream", chat)
    assert set_turn_model(agent, ERNIE)
    turn = _run(agent, "hello")
    assert turn.model_id == ERNIE
    assert sent == [("kios", ERNIE)]
    assert shown == [ERNIE]
    # one turn only, never persisted
    assert agent.cfg.model_id == DEFAULT_MODEL_ID
    assert agent.model.id == DEFAULT_MODEL_ID
    sent.clear()
    _run(agent, "again")
    assert sent == [("gwarden", DEFAULT_MODEL_ID)]


def test_no_router_or_adapt_module_assigns_the_main_model():
    """Only the explicit user commands in tui.py may assign cfg.model_id."""
    import re
    root = os.path.dirname(agent_mod.__file__)
    pat = re.compile(r"cfg\.model_id\s*=(?!=)")
    writers = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                for no, line in enumerate(fh, 1):
                    if pat.search(line):
                        writers.append((os.path.relpath(path, root), no))
    files = sorted({f for f, _ in writers})
    # config.py heals an unknown id back to the default at load time;
    # tui.py holds the user's explicit /model and Ctrl+T commands
    assert files == ["config.py", "tui.py"], writers


# --------------------------------------------------------------------------
# TUI: the picker never commits behind the user's back
# --------------------------------------------------------------------------

def _ui_harness(test_fn, agent=None):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.application.current import set_app
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from rich.console import Console
    from fullagent.tui import UI

    if agent is None:
        events = []
        agent = SimpleNamespace(
            autonomy=3, session_id="msw12345", messages=[], turns=[],
            model=config.model_by_id(DEFAULT_MODEL_ID),
            goal=SimpleNamespace(status=lambda: None),
            skill_forge=SimpleNamespace(registry={}),
            save_session=lambda: None, subagents=None,
            log=SimpleNamespace(append=lambda *a, **k: events.append((a, k))))
        agent.events = events

    async def main():
        with create_pipe_input() as inp, \
                create_app_session(input=inp, output=DummyOutput()):
            cfg = Config()
            cfg.extra = dict(cfg.extra or {})
            ui = UI(cfg, agent)
            ui.output = io.StringIO()
            ui.console = Console(file=ui.output, width=120,
                                 color_system=None)
            try:
                with set_app(ui.app):
                    return test_fn(ui)
            finally:
                ui.__dict__.pop("app", None)
    return asyncio.run(main())


def _press(ui, *keys):
    from prompt_toolkit.key_binding.key_processor import KeyPress
    kp = ui.app.key_processor
    for k in keys:
        if isinstance(k, KeyPress):
            kp.feed(k)
        elif len(k) == 1:
            kp.feed(KeyPress(k, k))
        else:
            kp.feed(KeyPress(k, ""))
    kp.process_keys()


def test_typing_while_picker_open_never_switches_model(home):
    """The exact pattern behind the report: Ctrl+T, then the user types
    their message, recalls history with the arrows and presses Enter."""
    from prompt_toolkit.keys import Keys

    def body(ui):
        sent = []
        ui._dispatch = sent.append
        _press(ui, Keys.ControlT)
        assert ui._overlay_open()
        _press(ui, *"hello")
        assert not ui._overlay_open()          # typing returned to the prompt
        _press(ui, Keys.Down, Keys.Down, Keys.Down, Keys.Enter)
        assert ui.cfg.model_id == DEFAULT_MODEL_ID
        assert sent == ["hello"]
    _ui_harness(body)


def test_deliberate_pick_is_announced_logged_and_shown(home):
    from prompt_toolkit.keys import Keys

    import threading

    def human_pause():  # a picker Enter is honoured only at human speed
        threading.Event().wait(0.5)

    def body(ui):
        _press(ui, Keys.ControlT)       # a lone key, as a human presses it
        _press(ui, Keys.Down)
        human_pause()
        _press(ui, Keys.Enter)
        assert ui.cfg.model_id == GEMINI
        assert ui._model().id == GEMINI          # status bar / header
        out = ui.output.getvalue()
        assert "Gemini 4 Argon" in out and GEMINI in out
        changes = [a for a, _k in ui.agent.events
                   if a and a[0] == "model.changed"]
        assert changes and changes[-1][1]["from"] == DEFAULT_MODEL_ID
        assert changes[-1][1]["to"] == GEMINI
        # re-selecting the current model changes and logs nothing
        n = len(changes)
        human_pause()
        _press(ui, Keys.ControlT)
        human_pause()
        _press(ui, Keys.Enter)
        assert "model unchanged" in ui._flash[0]
        assert ui.cfg.model_id == GEMINI
        assert len([a for a, _k in ui.agent.events
                    if a and a[0] == "model.changed"]) == n
    _ui_harness(body)


def test_status_bar_shows_the_model_the_running_turn_uses(home):
    def body(ui):
        ui.agent.turn_model_id = ERNIE          # pinned for the running turn
        assert ui._model().id == ERNIE
        ui.agent.turn_model_id = None
        assert ui._model().id == DEFAULT_MODEL_ID
    _ui_harness(body)
