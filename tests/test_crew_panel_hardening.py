"""Hardening tests for ParallelAgentsPanel (fullagent/tui.py).

Proves the panel NEVER crashes the TUI on malformed crew events:
- string / None / list / int payloads (the reported
  AttributeError: 'str' object has no attribute 'get' crash)
- agent dicts missing keys
- None error fields
- non-string event types, missing seqs, garbage event objects in poll_log
- garbage state injected directly into _agents

Every public entry point is exercised and must return normally —
no exception may escape.
"""
import sys
import types
from unittest.mock import MagicMock

# --- stub prompt_toolkit (not installed in this env; the panel under
# test doesn't need the real one) -------------------------------------------
class _PTPackage(types.ModuleType):
    """A fake package: any attribute/submodule access returns a MagicMock,
    and submodule imports (import a.b.c) succeed."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        mock = MagicMock(name=f"prompt_toolkit.{name}")
        setattr(self, name, mock)
        sys.modules[f"prompt_toolkit.{name}"] = mock
        return mock


_pt = _PTPackage("prompt_toolkit")
_pt.__path__ = []  # mark as a package so `import x.y` works
sys.modules["prompt_toolkit"] = _pt
# Pre-register every prompt_toolkit submodule tui.py imports, since the
# import system does not consult the package __getattr__ for submodules.
for _sub in ["application", "auto_suggest", "buffer", "completion",
             "enums", "filters", "formatted_text", "history",
             "key_binding", "keys", "layout", "patch_stdout",
             "search", "styles", "widgets"]:
    _m = types.ModuleType(f"prompt_toolkit.{_sub}")
    _m.__getattr__ = (lambda name, _s=_sub:  # noqa: B023
                      MagicMock(name=f"prompt_toolkit.{_s}.{name}"))
    sys.modules[f"prompt_toolkit.{_sub}"] = _m
    setattr(_pt, _sub, _m)
# --- stub rich (also not installed here; panel doesn't need it) --------------
_rich = _PTPackage("rich")
_rich.__path__ = []
sys.modules["rich"] = _rich
for _sub in ["console", "markdown", "panel", "syntax", "text"]:
    _m = types.ModuleType(f"rich.{_sub}")
    _m.__getattr__ = (lambda name, _s=_sub:  # noqa: B023
                      MagicMock(name=f"rich.{_s}.{name}"))
    sys.modules[f"rich.{_sub}"] = _m
    setattr(_rich, _sub, _m)

sys.path.insert(0, "/home/hatch/workspace/pytest-repo")
from fullagent.tui import ParallelAgentsPanel  # noqa: E402


class FakeEvent:
    """Minimal event-like object; attributes may be anything."""
    def __init__(self, type=None, data=None, seq=None):
        self.type = type
        self.data = data
        self.seq = seq


class FakeLog:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


MALFORMED_PAYLOADS = [
    "just a string",          # the reported crash payload
    "",                        # empty string (falsy)
    None,
    123,
    4.5,
    True,
    ["crew", "spawn"],         # list payload
    ("crew", "spawn"),         # tuple payload
    {"id": None},             # None id
    {"id": 123},              # int id
    {"id": ["x"]},            # list id
    {"id": {"k": "v"}},       # dict id
    {},                        # empty dict
    {"id": "a1", "nickname": None, "role": None, "task": None},
    {"id": "a1", "nickname": 42, "role": ["r"], "task": {"t": 1}},
    {"id": "a1", "step": "not-an-int", "tools": "bash"},
    {"id": "a1", "step": None, "tools": 42},      # non-iterable tools
    {"id": "a1", "step": {"s": 1}, "tools": {"t": 1}},  # dict tools
    {"id": "a1", "error": None},                   # None error
    {"id": "a1", "error": 500, "elapsed_ms": "fast"},
    {"id": "a1", "error": ["e1", "e2"], "elapsed_ms": None},
    {"id": "a1", "error": {"msg": "x"}, "files_touched": "nope"},
    {"id": "a1", "files_touched": None},
]

EV_TYPES = ["crew.spawn", "crew.progress", "crew.done",
            "crew.resumed", "crew.force_stop", "crew.closed",
            "crew.unknown", "", None, 123, b"crew.spawn"]


def test_ingest_never_raises_on_malformed():
    """Direct ingest() with every malformed payload x event type combo."""
    n = 0
    for et in EV_TYPES:
        for payload in MALFORMED_PAYLOADS:
            panel = ParallelAgentsPanel()
            # must not raise; return value must be a bool
            r1 = panel.ingest(et, payload)
            assert isinstance(r1, bool)
            r2 = panel.ingest(et, payload, seq="bad")
            assert isinstance(r2, bool)
            r3 = panel.ingest(et, payload, seq=None)
            assert isinstance(r3, bool)
            r4 = panel.ingest(et, payload, seq=3.5)
            assert isinstance(r4, bool)
            # render paths must also survive after the ingest
            panel.maybe_render(80, force=True)
            panel.render_text(80)
            n += 4
    print(f"  ingest combos survived: {n}")


def test_ingest_reproduces_reported_crash_shape():
    """The exact reported failure: string payload via crew.spawn/done."""
    panel = ParallelAgentsPanel()
    # Before the fix this raised:
    #   AttributeError: 'str' object has no attribute 'get'
    assert panel.ingest("crew.spawn", "boom") is False
    assert panel.ingest("crew.done", "boom") is False
    assert panel.ingest("crew.progress", "boom") is False
    assert panel.total_count == 0
    # panel still functional afterwards
    assert panel.ingest("crew.spawn", {"id": "ok1", "nickname": "n"}) is True
    assert panel.total_count == 1


def test_poll_log_with_garbage_events():
    """poll_log must survive event objects with bad/missing attrs."""
    evs = []
    seq = 100
    for et in EV_TYPES:
        for payload in MALFORMED_PAYLOADS:
            evs.append(FakeEvent(type=et, data=payload, seq=seq))
            seq += 1
    # event-likes with missing attributes entirely
    evs.append(FakeEvent())                    # all None
    evs.append(object())                       # no attrs at all
    evs.append(FakeEvent(type="crew.spawn", data={"id": "x"}, seq=True))
    evs.append(FakeEvent(type="crew.done", data={"id": "x"}, seq="9"))
    panel = ParallelAgentsPanel()
    panel.poll_log(FakeLog(evs))               # first poll: skips history
    panel2 = ParallelAgentsPanel()
    panel2._last_seq = -2                      # force the ingest path
    changed = panel2.poll_log(FakeLog(evs))
    assert isinstance(changed, bool)
    # renders still work
    panel2.tick_spinner()
    panel2.maybe_render(80, force=True)
    rows = panel2.render_text(80)
    assert isinstance(rows, list)
    print(f"  poll_log survived {len(evs)} garbage events, "
          f"rows={len(rows)}")


def test_poll_log_broken_log_object():
    panel = ParallelAgentsPanel()
    assert panel.poll_log(FakeLog([])) is False
    # log.events() itself raising must not propagate
    class BadLog:
        def events(self):
            raise RuntimeError("log exploded")
    assert panel.poll_log(BadLog()) is False


def test_poll_log_none_log():
    panel = ParallelAgentsPanel()
    try:
        panel.poll_log(None)
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"poll_log(None) raised {e!r}")


def test_missing_keys_state_renders():
    """Agent state dicts missing keys must render, not KeyError."""
    panel = ParallelAgentsPanel()
    panel.ingest("crew.spawn", {"id": "m1"})
    # surgically remove keys to simulate a corrupted/partial state
    st = panel._agents["m1"]
    for key in ["role", "task", "step", "tools", "spawn_ts",
                "elapsed_ms", "error", "files"]:
        st.pop(key, None)
    panel._dirty.add("m1")
    rows = panel.render_text(80)
    assert len(rows) == 1 and isinstance(rows[0], str)
    frags = panel.maybe_render(80, force=True)
    assert isinstance(frags, list)

    # every status branch with a bare-minimum state
    for status in (panel.RUNNING, panel.DONE, panel.ERROR,
                   panel.STOPPED, "weird-status"):
        panel2 = ParallelAgentsPanel()
        panel2._agents["z"] = {"id": "z", "status": status}
        panel2._order.append("z")
        panel2._dirty.add("z")
        rows = panel2.render_text(80)
        assert len(rows) == 1
        frags = panel2.maybe_render(80, force=True)
        assert isinstance(frags, list)
    # non-dict state entirely
    panel3 = ParallelAgentsPanel()
    panel3._agents["z"] = "not-a-dict"
    panel3._order.append("z")
    panel3._dirty.add("z")
    panel3.render_text(80)
    panel3.maybe_render(80, force=True)
    assert panel3.active_count == 0
    print("  missing-key / bad states all rendered")


def test_none_error_and_truncation_marker():
    """None errors -> no crash; long errors show an ellipsis, not a
    silently cut string."""
    panel = ParallelAgentsPanel()
    panel.ingest("crew.spawn", {"id": "e1", "nickname": "err-bot"})
    assert panel.ingest("crew.done",
                        {"id": "e1", "error": None}) is True
    st = panel._agents["e1"]
    assert st["status"] == panel.DONE and st["error"] == ""

    panel2 = ParallelAgentsPanel()
    panel2.ingest("crew.spawn", {"id": "e2"})
    long_err = "E" * 500
    panel2.ingest("crew.done", {"id": "e2", "error": long_err})
    st2 = panel2._agents["e2"]
    assert st2["status"] == panel2.ERROR
    # The FULL error is stored (error_detail() contract — a 120-char
    # head-cut at ingest would silently eat the diagnostic tail, e.g.
    # the attribute name in AttributeError). The rendered row fits to
    # panel width with an explicit … marker — never a silently cut
    # string, and the tail (diagnostic detail) is preserved.
    assert st2["error"] == long_err, "panel must store the full error"
    rows = panel2.render_text(200)
    assert "…" in rows[0], "rendered row must show the truncation marker"
    print("  None errors safe; full error stored, truncation marked …")


def test_tools_string_not_char_split():
    """A string 'tools' payload must not render as 'b, a, s, h'."""
    panel = ParallelAgentsPanel()
    panel.ingest("crew.spawn", {"id": "t1"})
    panel.ingest("crew.progress", {"id": "t1", "step": 2, "tools": "bash"})
    st = panel._agents["t1"]
    assert st["tools"] == ["bash"], f"got {st['tools']!r}"
    rows = panel.render_text(120)
    assert "bash" in rows[0] and "b, a, s, h" not in rows[0]


def test_queries_never_raise():
    panel = ParallelAgentsPanel()
    assert panel.active_count == 0
    assert panel.total_count == 0
    assert panel.has_activity() is False
    assert panel.maybe_render(80) is None
    assert panel.render_text(80) == []
    # force_stop / closed with garbage data
    for payload in MALFORMED_PAYLOADS:
        panel.ingest("crew.force_stop", payload)
        panel.ingest("crew.closed", payload)
        panel.ingest("crew.resumed", payload)
    panel.tick_spinner()
    print("  queries + lifecycle events all safe")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items())
               if k.startswith("test_")]:
        print(f"RUN {fn.__name__}")
        fn()
    print("ALL PANEL HARDENING TESTS PASSED")
