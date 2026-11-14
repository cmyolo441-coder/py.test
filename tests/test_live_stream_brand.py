"""Live stream pane, aurora prompt box, BITTU CHAUHAN signature and the
swarm dashboard upgrades (throughput sparkline, ETA, role colours)."""
import io
import threading
from types import SimpleNamespace

from rich.console import Console

from fullagent import cliui, fx, swarmdash as sd
from fullagent.agent import ToolEvent, Turn
from fullagent.tui import UI, _disp_width

C = {"border": "#5b6eae", "accent": "#c084fc", "cyan": "#22d3ee",
     "green": "#34d399", "yellow": "#facc15", "orange": "#fb923c",
     "red": "#f87171", "pink": "#f472b6", "fg": "#f8fafc", "dim": "#64748b"}


def _ui(width=80, reasoning=True, extra=None):
    ui = UI.__new__(UI)
    ui.cfg = SimpleNamespace(extra=dict(extra or {}),
                             show_reasoning=reasoning)
    ui._busy = False
    ui._cancel_flag = threading.Event()
    ui._focus_remaining = 0
    ui._status_text = ""
    ui._approve_request = None
    ui._spinner_i = 0
    ui._flash = None
    ui._turn_start_ts = 0
    ui._width = lambda: width
    ui._invalidate = lambda: None
    ui._start_spinner = lambda: None
    ui._stop_spinner = lambda: None
    ui._emit_user = lambda text: None
    ui._set_flash = lambda *a: None
    ui._print_turn_stats = lambda turn: None
    ui._pending_count = lambda: 0
    ui.output = io.StringIO()
    ui.console = Console(file=ui.output, width=width, color_system=None)
    ui.agent = SimpleNamespace(save_session=lambda: None)
    return ui


def _text(frags):
    return "".join(t for _, t in frags)


# -- live pane --------------------------------------------------------------

def test_thinking_streams_live_then_lands_above_the_reply():
    ui = _ui()
    seen = {}

    def run(text, on_token, on_reasoning, *a, **k):
        on_reasoning("weighing ")
        on_reasoning("the options")
        seen["think"] = _text(ui._live_fragments())
        seen["think_status"] = ui._status_text
        on_token("Answer ")
        seen["answer"] = _text(ui._live_fragments())
        seen["printed"] = ui.output.getvalue()
        return Turn(text, assistant_text="Answer ")

    ui.agent.run_turn = run
    ui._run_turn_thread("hi")
    # thoughts are on screen while the model is still thinking…
    assert "Thinking" in seen["think"]
    assert "weighing the options" in seen["think"]
    # …and the prompt box border only carries a phase label
    assert "weighing" not in seen["think_status"]
    # first answer token: the thinking block is committed, pane shows reply
    assert "Thinking" not in seen["answer"] and "Answer" in seen["answer"]
    assert "weighing the options" in seen["printed"]
    out = ui.output.getvalue()
    assert out.index("weighing") < out.index("Answer")
    assert ui._live_fragments() == []


def test_hidden_reasoning_never_reaches_the_pane():
    ui = _ui(reasoning=False)
    seen = []

    def run(text, on_token, on_reasoning, *a, **k):
        on_reasoning("secret thoughts")
        seen.append(_text(ui._live_fragments()))
        return Turn(text, assistant_text="")

    ui.agent.run_turn = run
    ui._run_turn_thread("hi")
    assert seen == [""]
    assert "secret" not in ui.output.getvalue()


def test_tool_call_hands_the_partial_line_to_the_transcript():
    ui = _ui()
    seen = []

    def run(text, on_token, on_reasoning, on_call, *a, **k):
        on_token("let me check")
        on_call(ToolEvent("list_dir", {"path": "."}))
        seen.append(_text(ui._live_fragments()))
        return Turn(text, assistant_text="let me check")

    ui.agent.run_turn = run
    ui._run_turn_thread("hi")
    assert seen == [""]
    assert ui.output.getvalue().count("let me check") == 1


def test_long_paragraph_wraps_on_words_and_is_height_capped():
    ui = _ui(width=40)
    ui._term_rows = lambda: 24
    words = " ".join(f"word{i}" for i in range(400))
    ui._live_set("answer", [words])
    rows = _text(ui._live_fragments()).split("\n")
    assert len(rows) <= 8                      # 24 rows // 3
    assert "more lines streaming above" in rows[0]
    for row in rows:
        assert _disp_width(row) <= 41          # width + the cursor cell
    assert "word399" in rows[-1]               # the newest text is visible
    for row in rows[1:-1]:                     # never split mid-word
        assert all(tok.startswith("word") and tok[4:].isdigit()
                   for tok in row.split())


def test_wrap_rows_counts_wide_characters_as_two_columns():
    rows = UI._wrap_rows("你好世界你好世界你好", 8)
    assert all(_disp_width(r) <= 8 for r in rows)
    assert "".join(rows) == "你好世界你好世界你好"


def test_committed_prose_is_word_wrapped_but_code_fences_are_not():
    ui = _ui(width=40)
    prose = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    code = "x = " + "1234567890" * 6

    def run(text, on_token, *a, **k):
        on_token(prose + "\n```\n" + code + "\n```\n")
        return Turn(text, assistant_text="")

    ui.agent.run_turn = run
    ui._run_turn_thread("hi")
    out = ui.output.getvalue()
    for line in out.split("\n"):
        if "alpha" in line or "kappa" in line:
            assert len(line) <= 40
    assert "epsilon" in out and "eps\nilon" not in out
    assert code in out                          # no fake newline inside code


# -- prompt box -------------------------------------------------------------

def test_aurora_border_keeps_every_row_at_exact_width():
    for width in (60, 80, 133):
        ui = _ui(width=width)
        plain = ui._bottom_fragments_plain()
        fancy = ui._bottom_fragments()
        assert _text(plain) == _text(fancy)
        assert _disp_width(_text(fancy)) == width
        assert any(style.startswith("#") for style, _ in fancy)
        ui._busy = True
        ui._status_text = "writing…"
        assert _disp_width(_text(ui._bottom_fragments())) == width


def test_aurora_border_can_be_switched_off():
    ui = _ui(extra={"aurora_border": False})
    assert ui._bottom_fragments() == ui._bottom_fragments_plain()


def test_aurora_gradient_loops_without_a_seam():
    assert fx.aurora_color(0.0) == fx.aurora_color(1.0)
    assert fx.lerp_hex("#000000", "#ffffff", 0.5) == "#808080"
    assert fx.lerp_hex("bad", "#ffffff", 0.5) == "bad"
    frags = fx.gradient_fragments("abc")
    assert _text(frags) == "abc" and len({s for s, _ in frags}) == 3


def test_placeholder_only_when_the_prompt_is_empty():
    ui = _ui()
    ui.buffer = SimpleNamespace(text="")
    assert "Ask anything" in _text(ui._placeholder_fragments())
    ui._busy = True
    assert "queue" in _text(ui._placeholder_fragments())
    ui.buffer.text = "typed"
    assert ui._placeholder_fragments() == []


# -- signature --------------------------------------------------------------

def test_signature_is_pinned_in_the_hud_at_every_width():
    for width in (120, 80, 60, 46):
        row = cliui.hud_fragments(width=width, colors=C, autonomy=3,
                                  skills=7)
        text = _text(row)
        assert "BITTU CHAUHAN" in text, (width, text)
        assert cliui.display_width(text) == width
    wide = _text(cliui.hud_fragments(width=120, colors=C, autonomy=3,
                                     skills=7))
    assert "7 skills" in wide


def test_signature_on_the_welcome_screen():
    for width, needle in ((84, "█▀▄ █ ▀█▀"), (40, "B I T T U  C H A U H A N")):
        out = io.StringIO()
        Console(file=out, width=width, color_system=None).print(
            cliui.welcome(width=width, version="1", model_label="m",
                          effort_label="high", signed_in="me", session="s",
                          cwd="~", colors=C))
        assert needle in out.getvalue()
        assert all(cliui.display_width(l) <= width
                   for l in out.getvalue().splitlines())
    assert all(cliui.display_width(l) == cliui.display_width(
        cliui.BRAND_ART[0]) for l in cliui.BRAND_ART)


# -- swarm dashboard --------------------------------------------------------

def _worker(i, state, role, pct):
    return {"id": f"sa-{i}", "name": f"{role}-{i}", "role": role,
            "state": state, "progress_pct": pct, "task": "t",
            "tokens_in": 100, "tokens_out": 10, "tokens_per_sec": 30.0,
            "run_ms": 1000, "tool_calls": 1}


def test_sparkline_and_eta_helpers():
    assert sd.sparkline([], 4) == "▁▁▁▁"
    assert sd.sparkline([0, 5, 10], 3) == "▁▅█"
    assert len(sd.sparkline(range(100), 12)) == 12
    assert sd.fmt_eta(50, 60) == "01:00"
    assert sd.fmt_eta(2, 60) == "" and sd.fmt_eta(100, 60) == ""
    assert sd.role_color("security", C) == C["red"]
    assert sd.role_color("nope", C) == C["cyan"]


def test_dashboard_shows_throughput_history_eta_and_slots():
    snap = {"workers": [_worker(1, "running", "coder", 50),
                        _worker(2, "queued", "tester", 0)],
            "active": 2, "max_active": 64, "max_workers": 12,
            "tokens_per_sec": 30.0, "swarm_started_at": 1000.0}
    st = sd.DashState()
    st.show()
    for k in range(8):
        rows = sd.render_dashboard(snap, width=110, colors=C, state=st,
                                   now=10.0 + k, wall_now=1060.0)
    text = "\n".join(_text(r) for r in rows)
    assert all(sd.row_width(r) == 110 for r in rows)
    assert "1/12 running" in text and "2/64 active" in text
    assert "ETA" in text
    assert any(ch in text for ch in "▂▃▄▅▆▇█")
    assert len(st.tps_hist) == 8
    # history is bounded no matter how long the swarm runs
    for k in range(500):
        st.note_tps(1.0, 100.0 + k)
    assert len(st.tps_hist) <= 4 * sd.SPARK_CELLS


def test_running_bar_glint_never_changes_the_bar_width():
    for now in (0.0, 0.11, 0.5, 3.3):
        frags = sd.bar_fragments(60, 10, C, "running", now=now)
        assert sd.row_width(frags) == 10
    plan = sd.BoardKeeper().update(
        {"workers": [_worker(1, "running", "coder", 50)]}, 1.0)
    rows = sd.compact_board({"workers": [_worker(1, "running", "coder", 50)]},
                            plan, width=90, colors=C, now=1.0)
    assert all(sd.row_width(r) == 90 for r in rows)
    assert "50%" in _text(rows[0])


def test_swarm_capacity_ceilings():
    from fullagent import subagents
    assert subagents.MAX_GROUP_SIZE == 32
    assert subagents.MAX_WORKERS_CAP == 32 and subagents.MAX_ACTIVE_CAP == 96
    assert subagents.MAX_STEPS == 40
