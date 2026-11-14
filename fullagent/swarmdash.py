"""swarmdash — live parallel-swarm dashboard + smooth compact board.

Pure rendering and timing logic for the terminal UI's swarm chrome. No
prompt_toolkit import and no I/O: callers pass a subagent ``snapshot()``
dict, the theme ``colors`` mapping (``fullagent.tui.C``) and a clock, and
get back prompt_toolkit ``(style, text)`` fragment rows. Every row is
padded/cut to EXACTLY the requested width, so the pinned layout can never
wrap, and every public renderer swallows junk input instead of raising
(render callbacks run inside prompt_toolkit's draw pass).

Pieces:

- ``render_dashboard``   the full live dashboard (Ctrl+G / ↓ / /swarm)
- ``compact_board``      the always-on board above the prompt
- ``BoardKeeper``        keeps the compact board at a STABLE height for the
                         life of a swarm, collapsing only after it settled
                         and a short hold (no jumping prompt box)
- ``DashState``          selection / expansion / scroll / eased progress
- ``RedrawGovernor``     coalesces worker-thread redraw requests into at
                         most ``max_fps`` frames (leading + trailing edge)
- ``AutoContinueGate``   debounced "results are waiting, agent is idle"
                         trigger for the automatic follow-up turn

Snapshot keys used (all read with ``.get`` and defaults, so the renderer
works with older managers too): top level ``workers``, ``active``,
``max_active``, ``tokens_per_sec``, ``swarm_started_at``,
``pending_results``; per worker ``id``, ``name``, ``role``, ``state``,
``task``, ``model``, ``current_action``, ``summary``, ``error``,
``progress_pct``, ``steps``, ``max_steps``, ``run_ms``, ``elapsed_ms``,
``tokens_in``, ``tokens_out``, ``tokens_per_sec``, ``tool_calls``,
``lines_added``, ``lines_removed``, ``files_touched``, ``recent``,
``wave``, ``retries``, ``depends_on``, ``delivered``.
"""

from __future__ import annotations

import math
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

Fragment = Tuple[str, str]
Row = List[Fragment]

ACTIVE_STATES = ("queued", "running", "cancelling")
FAILED_STATES = ("error", "blocked")
FINISHED_STATES = ("done", "error", "blocked", "cancelled")

SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
PARTIAL_BLOCKS = ("", "▏", "▎", "▍", "▌", "▋", "▊", "▉")

STATE_GLYPH = {
    "queued": "○",
    "running": "◉",
    "cancelling": "◌",
    "done": "✓",
    "blocked": "!",
    "error": "✗",
    "cancelled": "–",
}

STATE_COLOR = {
    "queued": "dim",
    "running": "green",
    "cancelling": "yellow",
    "done": "cyan",
    "blocked": "orange",
    "error": "red",
    "cancelled": "dim",
}

# Order on the dashboard: live work first, then the settled rows.
STATE_RANK = {"running": 0, "cancelling": 1, "queued": 2, "error": 3,
              "blocked": 4, "done": 5, "cancelled": 6}

SHORT_ROLE = {
    "researcher": "Explore", "coder": "Code", "tester": "Test",
    "reviewer": "Review", "analyst": "Analyze", "architect": "Architect",
    "debugger": "Debug", "optimizer": "Optimize", "refactorer": "Refactor",
    "documenter": "Docs", "devops": "DevOps", "integrator": "Integrate",
    "planner": "Plan", "security": "Security",
}

# Every specialist keeps its own chip colour (a palette key) so a big
# swarm reads at a glance: who is who never depends on reading the label.
ROLE_COLOR = {
    "researcher": "cyan", "coder": "green", "tester": "yellow",
    "reviewer": "accent", "analyst": "cyan", "architect": "pink",
    "debugger": "orange", "optimizer": "yellow", "refactorer": "green",
    "documenter": "fg", "devops": "orange", "integrator": "pink",
    "planner": "accent", "security": "red",
}

SPARK = "▁▂▃▄▅▆▇█"
SPARK_CELLS = 12         # throughput history cells in the stats row
SPARK_PERIOD_S = 0.5     # one history sample per this many seconds

PANEL_LIMIT = 8          # compact board: worker rows before "+N more"
BOARD_HOLD_S = 1.5       # compact board stays this long after settling
DETAIL_ROWS = 7          # fixed height of the expanded detail panel
SELECTED_BG = "bg:#2c3352"

FOOTER_KEYS = ("↑↓ select", "⏎ details", "x cancel", "X cancel all",
               "Esc close")


# ---------------------------------------------------------------------------
# Width helpers (self-contained: same East-Asian rule as fullagent.cliui)
# ---------------------------------------------------------------------------

def _cw(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: Any) -> int:
    return sum(_cw(ch) for ch in str(text))


def clean(text: Any, limit: int = 600) -> str:
    """One line of printable text (control chars and newlines removed)."""
    try:
        raw = str(text if text is not None else "")
    except Exception:  # noqa: BLE001 — a hostile __str__
        return ""
    raw = raw[: max(0, limit) * 2]
    out = []
    for ch in raw:
        if ch in "\r\n\t\v\f":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co"):
            continue
        else:
            out.append(ch)
    return " ".join("".join(out).split())[:limit]


def cut(text: str, cols: int) -> str:
    """Cut to at most ``cols`` columns, never splitting a wide glyph."""
    if cols <= 0:
        return ""
    out = []
    used = 0
    for ch in text:
        w = _cw(ch)
        if used + w > cols:
            break
        out.append(ch)
        used += w
    return "".join(out)


def ellipsize(text: str, cols: int) -> str:
    """Cut to ``cols`` columns with a trailing ellipsis when shortened."""
    if cols <= 0:
        return ""
    if display_width(text) <= cols:
        return text
    if cols == 1:
        return "…"
    return cut(text, cols - 1) + "…"


def fit(frags: Iterable[Fragment], width: int, pad_style: str = "") -> Row:
    """Pad or cut a fragment row to EXACTLY ``width`` columns."""
    width = max(0, int(width))
    out: Row = []
    used = 0
    for style, text in frags:
        if used >= width:
            break
        text = str(text)
        tw = display_width(text)
        if used + tw <= width:
            if text:
                out.append((style, text))
            used += tw
            continue
        piece = cut(text, width - used)
        if piece:
            out.append((style, piece))
            used += display_width(piece)
        break
    if used < width:
        out.append((pad_style, " " * (width - used)))
    return out


def row_width(row: Sequence[Fragment]) -> int:
    return sum(display_width(t) for _, t in row)


def short_role(role: Any) -> str:
    role = clean(role, 40).lower()
    if role in SHORT_ROLE:
        return SHORT_ROLE[role]
    return role.replace("_", " ").title() or "Agent"


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------

def role_color(role: Any, colors: dict) -> str:
    """Palette colour for a role chip (unknown roles fall back to cyan)."""
    key = ROLE_COLOR.get(str(role or "").strip().lower(), "cyan")
    return colors.get(key, "") or colors.get("cyan", "")


def sparkline(values: Sequence[float], cells: int) -> str:
    """The last ``cells`` samples as ▁▂▃▄▅▆▇█, scaled to their own peak,
    left-padded so the newest sample is always the right-most cell."""
    cells = max(0, int(cells))
    vals = [max(0.0, _num(v)) for v in list(values)[-cells:]] if cells else []
    peak = max(vals) if vals else 0.0
    out = []
    for v in vals:
        idx = 0 if peak <= 0 else int(round(v / peak * (len(SPARK) - 1)))
        out.append(SPARK[max(0, min(len(SPARK) - 1, idx))])
    return SPARK[0] * (cells - len(out)) + "".join(out)


def fmt_eta(overall_pct: float, elapsed_s: float) -> str:
    """Rough time left from progress so far; "" until it means something."""
    pct = _num(overall_pct)
    elapsed = _num(elapsed_s)
    if pct < 5.0 or pct >= 100.0 or elapsed < 3.0:
        return ""
    left = elapsed * (100.0 - pct) / pct
    if left > 6 * 3600:
        return ""
    return fmt_clock(left * 1000)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if math.isnan(n) or math.isinf(n):
        return default
    return n


def _int(value: Any, default: int = 0) -> int:
    return int(_num(value, default))


def fmt_count(value: Any) -> str:
    """1234 → 1.2k, 12345 → 12k, 1_250_000 → 1.2M."""
    n = max(0, _int(value))
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 10_000:
        return f"{n // 1000}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def fmt_clock(ms: Any) -> str:
    """Milliseconds → ``mm:ss`` (``h:mm:ss`` past an hour)."""
    total = max(0, int(_num(ms) // 1000))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def worker_state(w: dict) -> str:
    state = clean(w.get("state"), 20).lower()
    return state if state in STATE_GLYPH else "queued"


def progress_of(w: dict) -> float:
    """0..100 progress: ``progress_pct``, else steps/max_steps, else state."""
    state = worker_state(w)
    if state in FINISHED_STATES:
        return 100.0
    pct = w.get("progress_pct")
    if pct is not None and _num(pct, -1) >= 0:
        return max(0.0, min(100.0, _num(pct)))
    if state == "queued":
        return 0.0
    steps = _num(w.get("steps"))
    max_steps = _num(w.get("max_steps"), 0) or 20.0
    return max(0.0, min(99.0, steps * 100.0 / max(1.0, max_steps)))


def run_ms_of(w: dict, age_s: float = 0.0) -> float:
    """Running time: ``run_ms`` (falls back to ``elapsed_ms``), advanced by
    the age of the snapshot for live workers so timers tick smoothly
    between snapshot refreshes."""
    state = worker_state(w)
    if "run_ms" in w:
        ms = _num(w.get("run_ms"))
        if state == "queued":
            return 0.0
    else:
        ms = _num(w.get("elapsed_ms"))
    if state in ("running", "cancelling"):
        ms += max(0.0, min(5.0, _num(age_s))) * 1000.0
    return max(0.0, ms)


def action_of(w: dict) -> str:
    state = worker_state(w)
    if state == "done":
        text = clean(w.get("summary")) or "done"
        return text
    if state in FAILED_STATES:
        return clean(w.get("error")) or clean(w.get("summary")) or state
    if state == "cancelled":
        return clean(w.get("summary")) or "cancelled"
    action = clean(w.get("current_action"))
    if not action or action == "starting" or action.startswith("model step"):
        task = clean(w.get("task"))
        if action.startswith("model step") and task:
            return f"{task}"
        return action or task or state
    return action


def _workers(snapshot: Any) -> List[dict]:
    if not isinstance(snapshot, dict):
        return []
    raw = snapshot.get("workers")
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for i, w in enumerate(raw):
        if isinstance(w, dict):
            if "id" not in w or w.get("id") in (None, ""):
                w = dict(w)
                w["id"] = f"#{i}"
            out.append(w)
    return out


def wid(w: dict) -> str:
    return clean(w.get("id"), 80)


def dashboard_order(workers: Sequence[dict]) -> List[dict]:
    """Running first, then cancelling, queued, failed, done; stable."""
    indexed = list(enumerate(workers))
    indexed.sort(key=lambda p: (STATE_RANK.get(worker_state(p[1]), 9), p[0]))
    return [w for _, w in indexed]


# ---------------------------------------------------------------------------
# Progress bar
# ---------------------------------------------------------------------------

def bar_text(pct: Any, cells: int) -> Tuple[str, str]:
    """(filled, empty) strings for a smooth 1/8-cell progress bar.

    ``len(filled) + len(empty) == cells`` always (each glyph one column).
    """
    cells = max(0, int(cells))
    if cells == 0:
        return "", ""
    value = max(0.0, min(100.0, _num(pct)))
    eighths = int(round(value / 100.0 * cells * 8))
    full, part = divmod(eighths, 8)
    full = min(full, cells)
    filled = "█" * full
    if full < cells and part:
        filled += PARTIAL_BLOCKS[part]
    empty = "░" * (cells - len(filled))
    return filled, empty


def _gradient(colors: dict) -> List[str]:
    try:
        from .fx import RAINBOW
        pal = [c for c in RAINBOW if isinstance(c, str)]
        if pal:
            return pal
    except Exception:  # noqa: BLE001
        pass
    return [colors.get("accent", ""), colors.get("cyan", ""),
            colors.get("green", "")]


def bar_fragments(pct: Any, cells: int, colors: dict, state: str = "running",
                  palette: Optional[List[str]] = None,
                  now: Optional[float] = None) -> Row:
    """Gradient progress bar. With ``now`` a running bar gets a highlight
    that sweeps across the filled part, so live work visibly moves even
    between progress updates."""
    filled, empty = bar_text(pct, cells)
    out: Row = []
    glint = -1
    if now is not None and state == "running" and len(filled) >= 2:
        glint = int(_num(now) * 9) % (len(filled) + 5)
    if state in FAILED_STATES:
        tint = [colors.get("red", "")]
    elif state == "cancelled":
        tint = [colors.get("dim", "")]
    elif state == "done":
        tint = [colors.get("cyan", "")]
    else:
        tint = palette or _gradient(colors)
    n = max(1, cells)
    # group runs of the same colour into one fragment (cheaper to render)
    run_style, run_text = None, []
    for i, ch in enumerate(filled):
        color = tint[min(len(tint) - 1, int(i * len(tint) / n))]
        style = f"{color}"
        if i == glint:
            style = f"bold {colors.get('fg', '') or color}"
        if style != run_style and run_text:
            out.append((run_style or "", "".join(run_text)))
            run_text = []
        run_style = style
        run_text.append(ch)
    if run_text:
        out.append((run_style or "", "".join(run_text)))
    if empty:
        out.append((colors.get("dim", ""), empty))
    return out


# ---------------------------------------------------------------------------
# Dashboard state
# ---------------------------------------------------------------------------

@dataclass
class DashState:
    """Mutable view state of the live dashboard (owned by the UI)."""

    open: bool = False
    selected_id: str = ""
    expanded: bool = False
    top: int = 0
    reserved: int = 0
    order: List[str] = field(default_factory=list)
    eased: dict = field(default_factory=dict)
    tps_hist: List[float] = field(default_factory=list)
    tps_at: float = 0.0

    def note_tps(self, tps: Any, now: float) -> List[float]:
        """Record swarm throughput (at most one sample per period) and
        return the bounded history for the sparkline."""
        now = _num(now)
        if not self.tps_hist or now - self.tps_at >= SPARK_PERIOD_S \
                or now < self.tps_at:
            self.tps_hist.append(max(0.0, _num(tps)))
            self.tps_at = now
            if len(self.tps_hist) > 4 * SPARK_CELLS:
                del self.tps_hist[:-2 * SPARK_CELLS]
        return self.tps_hist

    def toggle(self) -> bool:
        if self.open:
            self.close()
        else:
            self.show()
        return self.open

    def show(self) -> None:
        self.open = True
        self.reserved = 0
        self.top = 0
        self.tps_hist = []
        self.tps_at = 0.0

    def close(self) -> None:
        self.open = False
        self.expanded = False
        self.reserved = 0

    def sync(self, ids: Sequence[str]) -> None:
        """Remember the current row order; keep the selection valid."""
        self.order = list(ids)
        if self.order and self.selected_id not in self.order:
            self.selected_id = self.order[0]
        if not self.order:
            self.selected_id = ""

    def move(self, delta: int) -> str:
        if not self.order:
            return ""
        try:
            idx = self.order.index(self.selected_id)
        except ValueError:
            idx = 0
        idx = max(0, min(len(self.order) - 1, idx + int(delta)))
        self.selected_id = self.order[idx]
        return self.selected_id

    def home(self) -> None:
        if self.order:
            self.selected_id = self.order[0]

    def end(self) -> None:
        if self.order:
            self.selected_id = self.order[-1]

    def ease(self, key: str, target: float, now: float,
             tau: float = 0.28) -> float:
        """Exponentially approach ``target`` so bars glide, never jump."""
        target = max(0.0, min(100.0, _num(target)))
        prev = self.eased.get(key)
        if prev is None:
            self.eased[key] = (target, now)
            return target
        value, ts = prev
        dt = max(0.0, now - ts)
        if target < value:            # a reset/retry: follow immediately
            value = target
        else:
            value = target - (target - value) * math.exp(-dt / max(0.01, tau))
            if abs(target - value) < 0.25:
                value = target
        self.eased[key] = (value, now)
        if len(self.eased) > 256:     # bounded: drop the stalest entries
            for k, _ in sorted(self.eased.items(),
                               key=lambda kv: kv[1][1])[:64]:
                self.eased.pop(k, None)
        return value


# ---------------------------------------------------------------------------
# Live dashboard
# ---------------------------------------------------------------------------

@dataclass
class _Cols:
    name: int = 14
    chip: int = 9
    bar: int = 10
    pct: bool = True
    tokens: int = 11
    tps: int = 7
    time: int = 7
    tools: int = 5
    lines: int = 11
    show: tuple = ("chip", "bar", "pct", "tokens", "tps", "tools", "lines")


def _plan_columns(inner: int, names: Sequence[str]) -> _Cols:
    cols = _Cols()
    longest = max([display_width(n) for n in names] + [6])
    cols.name = max(8, min(16, longest))
    show = list(cols.show)

    def fixed() -> int:
        total = 2 + cols.name + 1 + cols.time + 1  # glyph, name, time
        widths = {"chip": cols.chip, "bar": cols.bar, "pct": 5,
                  "tokens": cols.tokens, "tps": cols.tps,
                  "tools": cols.tools, "lines": cols.lines}
        for key in show:
            total += widths[key] + 1
        return total

    # Shed width in order of importance until the action has room: the
    # per-row extras first, then a narrower name and bar, then the token
    # and role columns; the clock and the action always stay.
    for victim in ("lines", "tools", "tps"):
        if inner - fixed() >= 16:
            break
        show.remove(victim)
    while inner - fixed() < 14 and cols.name > 10:
        cols.name -= 1
    if inner - fixed() < 12 and "bar" in show:
        cols.bar = 6
    for victim in ("tokens", "chip"):
        if inner - fixed() >= 10:
            break
        show.remove(victim)
    if inner - fixed() < 10 and "bar" in show:
        show.remove("bar")
    while inner - fixed() < 8 and cols.name > 6:
        cols.name -= 1
    if inner - fixed() < 6 and "pct" in show:
        show.remove("pct")
    cols.show = tuple(show)
    cols.pct = "pct" in show
    return cols


def _with_bg(row: Row, bg: str) -> Row:
    return [((f"{s} {bg}" if s else bg), t) for s, t in row]


def _worker_row(w: dict, *, inner: int, cols: _Cols, colors: dict,
                now: float, age_s: float, index: int, selected: bool,
                state_obj: Optional[DashState]) -> Row:
    state = worker_state(w)
    color = colors.get(STATE_COLOR.get(state, "fg"), "")
    finished = state in FINISHED_STATES
    dim = colors.get("dim", "")
    fg = dim if finished else colors.get("fg", "")
    if state == "running":
        glyph = SPINNER[(int(now * 12) + index * 3) % len(SPINNER)]
    else:
        glyph = STATE_GLYPH.get(state, "·")
    name = clean(w.get("name"), 60) or wid(w) or "agent"
    pct = progress_of(w)
    if state_obj is not None:
        pct = state_obj.ease(wid(w), pct, now)
    row: Row = [(f"bold {color}", glyph + " ")]
    name_style = (f"bold {colors.get('fg', '')}" if not finished else dim)
    row.append((name_style, _pad_cols(ellipsize(name, cols.name), cols.name)))
    row.append(("", " "))
    if "chip" in cols.show:
        chip = ellipsize(short_role(w.get("role")), cols.chip)
        chip_style = (f"bold {role_color(w.get('role'), colors)}"
                      if not finished else dim)
        row.append((chip_style, _pad_cols(chip, cols.chip)))
        row.append(("", " "))
    if "bar" in cols.show:
        row.extend(bar_fragments(pct, cols.bar, colors, state,
                                 now=now + index * 0.37))
        row.append(("", " "))
    if "pct" in cols.show:
        row.append((f"bold {color}" if not finished else dim,
                    f"{int(round(pct)):>3d}% "))
    # action takes whatever is left; right-hand metrics are fixed width
    right: Row = []
    if "tokens" in cols.show:
        tok = f"{fmt_count(w.get('tokens_in'))}→{fmt_count(w.get('tokens_out'))}"
        right.append(("", " "))
        right.append((dim if finished else colors.get("accent", ""),
                       _rjust_cols(ellipsize(tok, cols.tokens), cols.tokens)))
    if "tps" in cols.show:
        tps = _num(w.get("tokens_per_sec"))
        text = f"{tps:.0f}t/s" if state == "running" and tps > 0 else "–"
        right.append(("", " "))
        right.append((dim, _rjust_cols(ellipsize(text, cols.tps), cols.tps)))
    right.append(("", " "))
    clock = fmt_clock(run_ms_of(w, age_s))
    right.append((colors.get("yellow", "") if state == "running" else dim,
                  _rjust_cols(clock, cols.time)))
    if "tools" in cols.show:
        right.append(("", " "))
        right.append((dim, _rjust_cols(f"⚙{_int(w.get('tool_calls'))}",
                                       cols.tools)))
    if "lines" in cols.show:
        add = _int(w.get("lines_added"))
        rem = _int(w.get("lines_removed"))
        right.append(("", " "))
        if add or rem:
            plus = f"+{fmt_count(add)}"
            minus = f"−{fmt_count(rem)}"
            body = _rjust_cols(f"{plus} {minus}", cols.lines)
            lead = body[: len(body) - len(f"{plus} {minus}")]
            right.append(("", lead))
            right.append((colors.get("green", "") if not finished else dim,
                          plus))
            right.append(("", " "))
            right.append((colors.get("red", "") if not finished else dim,
                          minus))
        else:
            right.append((dim, _rjust_cols("±0", cols.lines)))
    used = row_width(row) + row_width(right)
    room = max(0, inner - used)
    action = ellipsize(action_of(w), room)
    action_style = (colors.get("red", "") if state in FAILED_STATES
                    else fg if not finished else dim)
    row.append((action_style, _pad_cols(action, room)))
    row.extend(right)
    row = fit(row, inner)
    if selected:
        row = _with_bg(row, SELECTED_BG)
    return row


def _pad_cols(text: str, cols: int) -> str:
    return text + " " * max(0, cols - display_width(text))


def _rjust_cols(text: str, cols: int) -> str:
    return " " * max(0, cols - display_width(text)) + text


def _boxed(inner_row: Row, colors: dict, inner: int,
           border_style: str) -> Row:
    return ([(border_style, "│ ")] + fit(inner_row, inner)
            + [(border_style, " │")])


def _hline(left: str, fill: str, right: str, width: int, style: str) -> Row:
    return [(style, left + fill * max(0, width - 2) + right)]


def _title_line(width: int, colors: dict, right_text: str,
                border_style: str, now: float = 0.0,
                live: bool = False) -> Row:
    """╭─ ⚡ SWARM ───────── 02:31 ─╮ with a gradient title that flows
    through the palette while the swarm is live."""
    title = " ⚡ SWARM "
    pal = _gradient(colors)
    frags: Row = [(border_style, "╭─")]
    n = max(1, len(title))
    shift = int(_num(now) * 6) if live else 0
    for i, ch in enumerate(title):
        base = min(len(pal) - 1, int(i * len(pal) / n))
        frags.append((f"bold {pal[(base + shift) % len(pal)]}", ch))
    right = f" {right_text} " if right_text else ""
    used = 2 + display_width(title) + display_width(right) + 2
    if used > width:
        right = ""
        used = 2 + display_width(title) + 2
    frags.append((border_style, "─" * max(0, width - used)))
    if right:
        frags.append((f"bold {colors.get('yellow', '')}", right))
    frags.append((border_style, "─╮"))
    return fit(frags, width)


def _stats_rows(shown: Sequence[dict], snapshot: dict, *, inner: int,
                colors: dict, now_wall: float,
                state: Optional["DashState"] = None,
                now: float = 0.0) -> Tuple[Row, Row, str]:
    counts = {s: 0 for s in STATE_GLYPH}
    for w in shown:
        counts[worker_state(w)] += 1
    failed = counts["error"] + counts["blocked"]
    active = _int(snapshot.get("active"),
                  counts["running"] + counts["queued"] + counts["cancelling"])
    max_active = _int(snapshot.get("max_active"), 0)
    dim = colors.get("dim", "")
    pieces: Row = [
        (f"bold {colors.get('pink', '')}",
         f"◉ {active}/{max_active or '?'} active"),
        (dim, "  "),
        (f"bold {colors.get('green', '')}",
         f"● {counts['running']}"
         + (f"/{_int(snapshot.get('max_workers'))}"
            if _int(snapshot.get("max_workers")) else "") + " running"),
        (dim, "  "),
        (colors.get("fg", ""), f"○ {counts['queued']} queued"),
        (dim, "  "),
        (f"bold {colors.get('cyan', '')}", f"✓ {counts['done']} done"),
        (dim, "  "),
        (f"bold {colors.get('red', '')}" if failed else dim,
         f"✗ {failed} failed"),
    ]
    if counts["cancelled"] or counts["cancelling"]:
        pieces += [(dim, "  "),
                   (colors.get("yellow", ""),
                    f"– {counts['cancelled'] + counts['cancelling']} "
                    f"cancelled")]
    # drop trailing pieces cleanly (never cut a label mid-word)
    while row_width(pieces) > inner and len(pieces) > 1:
        pieces = pieces[:-2] if len(pieces) > 2 else pieces[:-1]
    row_a = pieces

    tin = sum(_int(w.get("tokens_in")) for w in shown)
    tout = sum(_int(w.get("tokens_out")) for w in shown)
    if not shown:
        tin, tout = _int(snapshot.get("tokens_in")), _int(snapshot.get("tokens_out"))
    tps = snapshot.get("tokens_per_sec")
    if tps is None:
        tps = sum(_num(w.get("tokens_per_sec")) for w in shown
                  if worker_state(w) == "running")
    pending = _int(snapshot.get("pending_results"))
    overall = (sum(progress_of(w) for w in shown) / len(shown)) if shown else 0
    right: Row = [
        (colors.get("accent", ""), f" {fmt_count(tin)}→{fmt_count(tout)} tok"),
        (dim, " · "),
        (f"bold {colors.get('green', '')}", f"{_num(tps):.0f} tok/s"),
    ]
    live_any = any(worker_state(w) in ACTIVE_STATES for w in shown)
    if state is not None and inner >= 84:
        # throughput history: the swarm's pulse over the last few seconds
        hist = (state.note_tps(tps, now) if live_any else state.tps_hist)
        if hist:
            right += [(dim, " "),
                      (colors.get("green", "") if live_any else dim,
                       sparkline(hist, SPARK_CELLS))]
    if pending:
        right += [(dim, " · "),
                  (f"bold {colors.get('yellow', '')}",
                   f"⬇ {pending} result{'s' if pending != 1 else ''} "
                   f"→ main")]
    bar_cells = inner - row_width(right) - 6
    if bar_cells < 6:
        # narrow: shed the tok/s, then the pending marker
        right = right[:1] + right[3:] if pending else right[:1]
        bar_cells = inner - row_width(right) - 6
    row_b: Row = []
    if bar_cells >= 4:
        row_b.extend(bar_fragments(overall, bar_cells, colors, "running"))
        row_b.append((f"bold {colors.get('fg', '')}",
                      f" {int(round(overall)):>3d}% "))
    row_b.extend(right)

    started = _num(snapshot.get("swarm_started_at"))
    if started > 0:
        elapsed = max(0.0, now_wall - started)
        wall = fmt_clock(elapsed * 1000)
        eta = fmt_eta(overall, elapsed) if live_any else ""
        if eta and inner >= 56:
            wall = f"{wall} · ETA {eta}"
    else:
        live = [run_ms_of(w) for w in shown
                if worker_state(w) in ACTIVE_STATES]
        if live:
            wall = fmt_clock(max(_num(w.get("elapsed_ms")) for w in shown
                                 if worker_state(w) in ACTIVE_STATES))
        elif shown:
            wall = "settled"
        else:
            wall = ""
    return row_a, row_b, wall


def _wrap(text: str, cols: int, rows: int) -> List[str]:
    out: List[str] = []
    rest = text
    while rest and len(out) < rows:
        piece = cut(rest, cols)
        if not piece:
            break
        if len(out) == rows - 1 and display_width(rest) > cols:
            piece = ellipsize(rest, cols)
            out.append(piece)
            break
        out.append(piece)
        rest = rest[len(piece):].lstrip()
    return out


def _detail_rows(w: Optional[dict], *, inner: int, colors: dict,
                 age_s: float) -> List[Row]:
    dim = colors.get("dim", "")
    fg = colors.get("fg", "")
    rows: List[Row] = []
    if not w:
        rows.append([(dim, "no worker selected")])
    else:
        state = worker_state(w)
        color = colors.get(STATE_COLOR.get(state, "fg"), "")
        head: Row = [
            (f"bold {color}", f"{STATE_GLYPH.get(state, '·')} "),
            (f"bold {fg}", clean(w.get("name"), 60) or wid(w)),
            (dim, f" · {short_role(w.get('role'))}"),
            (dim, f" · {state}"),
        ]
        model = clean(w.get("model"), 40)
        if model:
            head.append((dim, f" · {model}"))
        steps = _int(w.get("steps"))
        max_steps = _int(w.get("max_steps"))
        head.append((dim, f" · step {steps}/{max_steps or '?'}"))
        if _int(w.get("wave")):
            head.append((dim, f" · wave {_int(w.get('wave'))}"))
        if _int(w.get("retries")):
            head.append((colors.get("yellow", ""),
                         f" · retry {_int(w.get('retries'))}"))
        if w.get("delivered") is True and state in FINISHED_STATES:
            head.append((colors.get("green", ""), " · delivered"))
        rows.append(head)
        task = clean(w.get("task"), 2000) or "(no task)"
        label = "task  "
        for i, line in enumerate(_wrap(task, max(8, inner - 6), 2)):
            rows.append([(dim, label if i == 0 else "      "), (fg, line)])
        recent = w.get("recent")
        if isinstance(recent, (list, tuple)) and recent:
            items = [clean(x, 200) for x in list(recent)[-3:]][::-1]
            for i, item in enumerate(items):
                rows.append([(dim, "recent" if i == 0 else "      "),
                             (colors.get("cyan", ""), " › "),
                             (fg, ellipsize(item, max(4, inner - 9)))])
        else:
            rows.append([(dim, "now   "),
                         (fg, ellipsize(action_of(w), max(4, inner - 6)))])
        files = w.get("files_touched")
        if isinstance(files, (list, tuple)) and files:
            names = [clean(f, 120) for f in files]
            text = ", ".join(names[:6])
            if len(names) > 6:
                text += f" (+{len(names) - 6})"
            rows.append([(dim, "files "), (colors.get("accent", ""),
                                          ellipsize(text, max(4, inner - 6)))])
        if state in FAILED_STATES and clean(w.get("error")):
            rows.append([(dim, "error "),
                         (colors.get("red", ""),
                          ellipsize(clean(w.get("error")), max(4, inner - 6)))])
        elif clean(w.get("summary")):
            rows.append([(dim, "result"),
                         (fg, " " + ellipsize(clean(w.get("summary")),
                                              max(4, inner - 7)))])
    rows = rows[:DETAIL_ROWS]
    while len(rows) < DETAIL_ROWS:
        rows.append([])
    return rows


def visible_workers(snapshot: Any, only_ids: Optional[Iterable[str]] = None,
                    history: int = 24) -> List[dict]:
    """Workers the dashboard lists: the current swarm, not old history."""
    workers = _workers(snapshot)
    if only_ids:
        keep = set(only_ids)
        chosen = [w for w in workers
                  if wid(w) in keep or worker_state(w) in ACTIVE_STATES]
        if chosen:
            return chosen
    active = [w for w in workers if worker_state(w) in ACTIVE_STATES]
    settled = [w for w in workers if worker_state(w) not in ACTIVE_STATES]
    room = max(0, history - len(active))
    return active + (settled[-room:] if room else [])


def render_dashboard(snapshot: Any, *, width: int, colors: Optional[dict] = None,
                     state: Optional[DashState] = None,
                     now: Optional[float] = None,
                     wall_now: Optional[float] = None,
                     max_list_rows: int = 10, age_s: float = 0.0,
                     only_ids: Optional[Iterable[str]] = None) -> List[Row]:
    """The live swarm dashboard as fragment rows, each EXACTLY ``width``.

    Never raises: junk input renders an empty dashboard, and an internal
    failure degrades to a one-line framed notice of the same width.
    """
    colors = colors if isinstance(colors, dict) else {}
    try:
        width = max(1, int(width))
    except (TypeError, ValueError):
        width = 80
    try:
        return _render_dashboard(
            snapshot, width=width, colors=colors, state=state,
            now=time.monotonic() if now is None else _num(now),
            wall_now=time.time() if wall_now is None else _num(wall_now),
            max_list_rows=max(1, _int(max_list_rows, 10)),
            age_s=_num(age_s), only_ids=only_ids)
    except Exception as exc:  # noqa: BLE001 — chrome must never crash
        border = colors.get("border", "")
        msg = f"swarm dashboard unavailable ({type(exc).__name__})"
        if width < 4:
            return [fit([("", msg)], width)]
        return [_hline("╭", "─", "╮", width, border),
                _boxed([(colors.get("red", ""), msg)], colors, width - 4,
                       border),
                _hline("╰", "─", "╯", width, border)]


def _render_dashboard(snapshot: Any, *, width: int, colors: dict,
                      state: Optional[DashState], now: float, wall_now: float,
                      max_list_rows: int, age_s: float,
                      only_ids: Optional[Iterable[str]]) -> List[Row]:
    border = colors.get("border", "")
    dim = colors.get("dim", "")
    snap = snapshot if isinstance(snapshot, dict) else {}
    if width < 12:
        return [fit([(f"bold {colors.get('pink', '')}", "⚡ swarm")], width)]
    inner = width - 4
    shown = dashboard_order(visible_workers(snap, only_ids))
    ids = [wid(w) for w in shown]
    if state is not None:
        state.sync(ids)
    selected = state.selected_id if state is not None else (ids[0] if ids else "")

    rows: List[Row] = []
    row_a, row_b, wall = _stats_rows(shown, snap, inner=inner, colors=colors,
                                     now_wall=wall_now, state=state, now=now)
    rows.append(_title_line(
        width, colors, wall, border, now=now,
        live=any(worker_state(w) in ACTIVE_STATES for w in shown)))
    rows.append(_boxed(row_a, colors, inner, border))
    rows.append(_boxed(row_b, colors, inner, border))

    # list window: stable height while open (only ever grows)
    list_rows = max(1, min(max_list_rows, max(1, len(shown))))
    if state is not None:
        state.reserved = max(state.reserved, list_rows)
        list_rows = min(max(list_rows, state.reserved), max_list_rows)
    top = state.top if state is not None else 0
    if selected in ids:
        sel_idx = ids.index(selected)
        if sel_idx < top:
            top = sel_idx
        elif sel_idx >= top + list_rows:
            top = sel_idx - list_rows + 1
    top = max(0, min(top, max(0, len(shown) - list_rows)))
    if state is not None:
        state.top = top

    above = top
    below = max(0, len(shown) - (top + list_rows))
    sep_label = ""
    if above:
        sep_label = f" ↑ {above} more "
    rows.append(_sep(width, border, dim, sep_label))
    cols = _plan_columns(inner, [clean(w.get("name"), 60) or wid(w)
                                 for w in shown])
    if not shown:
        rows.append(_boxed([(dim, "no subagents yet — the main agent spawns "
                                  "them with TaskGroup")], colors, inner,
                           border))
        for _ in range(list_rows - 1):
            rows.append(_boxed([], colors, inner, border))
    else:
        window = shown[top:top + list_rows]
        for i, w in enumerate(window):
            rows.append(_boxed(_worker_row(
                w, inner=inner, cols=cols, colors=colors, now=now,
                age_s=age_s, index=top + i, selected=wid(w) == selected,
                state_obj=state), colors, inner, border))
        for _ in range(list_rows - len(window)):
            rows.append(_boxed([], colors, inner, border))

    if state is not None and state.expanded:
        target = next((w for w in shown if wid(w) == selected), None)
        rows.append(_sep(width, border, dim, " details "))
        for drow in _detail_rows(target, inner=inner, colors=colors,
                                 age_s=age_s):
            rows.append(_boxed(drow, colors, inner, border))

    rows.append(_footer(width, colors, border, below,
                        position=(ids.index(selected) + 1
                                  if selected in ids else 0),
                        total=len(ids)))
    return [fit(r, width) for r in rows]


def _sep(width: int, border: str, dim: str, label: str = "") -> Row:
    if not label or display_width(label) + 4 > width:
        return _hline("├", "─", "┤", width, border)
    fill = width - 3 - display_width(label)
    return [(border, "├─"), (dim, label), (border, "─" * max(0, fill)),
            (border, "┤")]


def _footer(width: int, colors: dict, border: str, below: int,
            position: int, total: int) -> Row:
    """╰─ ↑↓ select · ⏎ details · x cancel … ─── 3/12 ─╯ (keys that fit)."""
    dim = colors.get("dim", "")
    key = f"bold {colors.get('cyan', '')}"
    right = f" {position}/{total} " if total else ""
    if below:
        right = f" ↓ {below} more ·{right}" if right else f" ↓ {below} more "
    if display_width(right) + 6 > width:
        right = ""
    room = width - 4 - display_width(right) - 1
    keys: Row = []
    for k in FOOTER_KEYS:
        glyph, _, label = k.partition(" ")
        piece: Row = ([(dim, " ·")] if keys else []) + [
            ("", " "), (key, glyph), (dim, " " + label)]
        if row_width(keys) + row_width(piece) > room:
            break
        keys.extend(piece)
    frags: Row = [(border, "╰─")] + keys + [("", " ")]
    fill = width - row_width(frags) - display_width(right) - 2
    frags.append((border, "─" * max(0, fill)))
    if right:
        frags.append((dim, right))
    frags.append((border, "─╯"))
    if row_width(frags) > width:
        return fit(frags, width - 1) + [(border, "╯")]
    return fit(frags, width)


# ---------------------------------------------------------------------------
# Compact board (always-on, above the prompt) — stable height
# ---------------------------------------------------------------------------

@dataclass
class BoardPlan:
    visible: bool = False
    ids: List[str] = field(default_factory=list)
    height: int = 0
    settled: bool = False


class BoardKeeper:
    """Keeps the compact board's height stable for the life of a swarm.

    The board reserves one row per worker of the current swarm (up to
    ``limit``, plus the header and a "+N more" line). Rows never disappear
    while any worker is still live — finished workers stay as dim ✓/✗ rows
    — and the whole board collapses only once everything settled AND
    ``hold`` seconds passed. The height can only grow within a swarm, so
    the prompt box never bounces when workers flip between states.
    """

    def __init__(self, hold: float = BOARD_HOLD_S, limit: int = PANEL_LIMIT):
        self.hold = float(hold)
        self.limit = max(1, int(limit))
        self._ids: dict = {}          # id -> first-seen order (insertion)
        self._height = 0
        self._settled_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def epoch_ids(self) -> List[str]:
        with self._lock:
            return list(self._ids)

    def reset(self) -> None:
        with self._lock:
            self._ids.clear()
            self._height = 0
            self._settled_at = None

    def update(self, snapshot: Any, now: float) -> BoardPlan:
        workers = _workers(snapshot)
        active = [wid(w) for w in workers if worker_state(w) in ACTIVE_STATES]
        with self._lock:
            if active:
                for aid in active:
                    if aid not in self._ids:
                        self._ids[aid] = len(self._ids)
                self._settled_at = None
            else:
                if not self._ids:
                    return BoardPlan()
                if self._settled_at is None:
                    self._settled_at = now
                if now - self._settled_at >= self.hold:
                    self._ids.clear()
                    self._height = 0
                    self._settled_at = None
                    return BoardPlan()
            n = len(self._ids)
            rows = 1 + min(n, self.limit) + (1 if n > self.limit else 0)
            self._height = max(self._height, rows)
            return BoardPlan(visible=True, ids=list(self._ids),
                             height=self._height,
                             settled=self._settled_at is not None)


def compact_board(snapshot: Any, plan: BoardPlan, *, width: int,
                  colors: Optional[dict] = None,
                  now: Optional[float] = None, age_s: float = 0.0) -> List[Row]:
    """Rows for the always-on board: exactly ``plan.height`` rows, each
    exactly ``width`` columns. Empty when the plan is not visible."""
    colors = colors if isinstance(colors, dict) else {}
    if not isinstance(plan, BoardPlan) or not plan.visible:
        return []
    try:
        width = max(1, int(width))
    except (TypeError, ValueError):
        width = 80
    now = time.monotonic() if now is None else _num(now)
    try:
        rows = _compact_rows(snapshot, plan, width=width, colors=colors,
                             now=now, age_s=age_s)
    except Exception:  # noqa: BLE001 — chrome must never crash
        rows = [fit([(colors.get("dim", ""), " ≡ swarm")], width)]
    rows = rows[:max(1, plan.height)]
    while len(rows) < plan.height:
        rows.append(fit([], width))
    return rows


def _compact_rows(snapshot: Any, plan: BoardPlan, *, width: int, colors: dict,
                  now: float, age_s: float) -> List[Row]:
    workers = _workers(snapshot)
    keep = set(plan.ids)
    members = [w for w in workers
               if wid(w) in keep or worker_state(w) in ACTIVE_STATES]
    order = {aid: i for i, aid in enumerate(plan.ids)}
    members.sort(key=lambda w: order.get(wid(w), len(order)))
    total = len(members)
    counts: dict = {}
    for w in members:
        s = worker_state(w)
        counts[s] = counts.get(s, 0) + 1
    parts = []
    for key, label in (("running", "running"), ("queued", "queued"),
                       ("cancelling", "cancelling"), ("done", "done"),
                       ("error", "failed"), ("blocked", "blocked"),
                       ("cancelled", "cancelled")):
        if counts.get(key):
            parts.append(f"{counts[key]} {label}")
    noun = "Agent" if total == 1 else "Agents"
    pal = _gradient(colors)
    head: Row = [
        (f"bold {pal[0]}", f" ≡ {total} {noun} "),
        (f"bold {pal[min(3, len(pal) - 1)]}",
         "(" + " · ".join(parts) + ") " if parts else ""),
    ]
    if plan.settled:
        head.append((f"bold {colors.get('green', '')}", "✓ settled "))
    tail: Row = [(colors.get("dim", ""), " ↓ to manage · ^G ")]
    if width < 48:
        tail = [(colors.get("dim", ""), " ↓ to manage ")]
    if width >= 72 and members:
        # whole-swarm progress at a glance, right in the header
        overall = sum(progress_of(w) for w in members) / len(members)
        tail = ([("", " ")]
                + bar_fragments(overall, 10, colors,
                                "done" if plan.settled else "running",
                                now=None if plan.settled else now)
                + [(f"bold {colors.get('fg', '')}",
                    f" {int(round(overall)):>3d}%")] + tail)
    rows: List[Row] = [_align(head, tail, width, "─",
                              colors.get("border", ""))]

    limit = max(1, plan.height - 1)
    if total > limit:
        limit = max(1, limit - 1)
        # live work wins the visible slots, spawn order inside each group
        ranked = sorted(enumerate(members),
                        key=lambda p: (0 if worker_state(p[1]) in
                                       ACTIVE_STATES else 1, p[0]))
        chosen_ids = {wid(w) for _, w in ranked[:limit]}
        chosen = [w for w in members if wid(w) in chosen_ids]
    else:
        chosen = members
    for i, w in enumerate(chosen):
        rows.append(_compact_row(w, width=width, colors=colors, now=now,
                                 age_s=age_s, index=i))
    hidden = total - len(chosen)
    if hidden > 0:
        rows.append(fit([(colors.get("dim", ""),
                          f"   … +{hidden} more · ↓ to manage")], width))
    return rows


def _align(left: Row, right: Row, width: int, fill: str,
           fill_style: str = "") -> Row:
    lw, rw = row_width(left), row_width(right)
    if lw + rw + 1 > width:
        right = fit(right, max(0, min(rw, width // 2)))
        left = fit(left, max(0, width - row_width(right) - 1))
        lw, rw = row_width(left), row_width(right)
    gap = max(0, width - lw - rw)
    return fit(list(left) + [(fill_style, fill * gap)] + list(right), width)


def _compact_row(w: dict, *, width: int, colors: dict, now: float,
                 age_s: float, index: int) -> Row:
    state = worker_state(w)
    finished = state in FINISHED_STATES
    dim = colors.get("dim", "")
    color = colors.get(STATE_COLOR.get(state, "fg"), "")
    if state == "running":
        # breathing glyph: ◉ pulses through the gradient
        pal = _gradient(colors)
        color = pal[(int(now * 4) + index) % len(pal)]
    icon = STATE_GLYPH.get(state, "·")
    chip = short_role(w.get("role"))
    left: Row = [
        (f"bold {color}", f" {icon} "),
        (dim if finished else f"bold {role_color(w.get('role'), colors)}",
         chip),
        (dim, " · "),
    ]
    right: Row = []
    if width >= 56:
        right.extend(bar_fragments(progress_of(w), 8, colors, state,
                                   now=now + index * 0.37))
        right.append((dim, " "))
    if width >= 44:
        right.append((colors.get("yellow", "") if state == "running" else dim,
                      fmt_clock(run_ms_of(w, age_s))))
        retries = _int(w.get("retries"))
        if retries:
            right.append((colors.get("yellow", ""), f" ↻{retries}"))
        right.append(("", " "))
    room = max(4, width - row_width(left) - row_width(right))
    action = ellipsize(action_of(w), room)
    left.append((dim if finished else colors.get("fg", ""),
                 _pad_cols(action, room)))
    return fit(left + right, width)


# ---------------------------------------------------------------------------
# Redraw coalescing
# ---------------------------------------------------------------------------

class RedrawGovernor:
    """Coalesce redraw requests from many threads into <= ``max_fps``.

    ``request()`` fires immediately when the last frame is older than the
    frame interval (leading edge — a lone event shows instantly) and
    otherwise only marks a frame as pending; the owner's pulse thread calls
    ``pump()`` when ``due_in()`` reaches zero (trailing edge — the last
    event of a burst is never lost). ``tick()`` forces an animation frame
    (spinners/timers) without exceeding the same cap. Thread-safe; the
    ``fire`` callback runs outside the lock.
    """

    def __init__(self, fire: Callable[[], None], max_fps: float = 12.0,
                 clock: Callable[[], float] = time.monotonic):
        self._fire = fire
        self.min_interval = 1.0 / max(1.0, float(max_fps))
        self._clock = clock
        self._lock = threading.Lock()
        self._last = -1e18
        self._pending = False
        self.requests = 0
        self.fires = 0

    @property
    def last_fire(self) -> float:
        return self._last

    @property
    def pending(self) -> bool:
        return self._pending

    def _do_fire(self) -> None:
        try:
            self._fire()
        except Exception:  # noqa: BLE001 — a redraw must never kill a worker
            pass

    def request(self) -> str:
        """'fired' | 'scheduled' (first coalesced) | 'coalesced'."""
        now = self._clock()
        with self._lock:
            self.requests += 1
            if self._pending:
                return "coalesced"
            if now - self._last >= self.min_interval:
                self._last = now
                self.fires += 1
            else:
                self._pending = True
                return "scheduled"
        self._do_fire()
        return "fired"

    def due_in(self, now: Optional[float] = None) -> Optional[float]:
        now = self._clock() if now is None else now
        with self._lock:
            if not self._pending:
                return None
            return max(0.0, self._last + self.min_interval - now)

    def pump(self, now: Optional[float] = None) -> bool:
        now = self._clock() if now is None else now
        with self._lock:
            if not self._pending or now - self._last < self.min_interval:
                return False
            self._pending = False
            self._last = now
            self.fires += 1
        self._do_fire()
        return True

    def tick(self, interval: float, now: Optional[float] = None) -> bool:
        """Animation frame when the last frame is older than ``interval``."""
        now = self._clock() if now is None else now
        with self._lock:
            if now - self._last < max(self.min_interval, interval):
                return False
            self._pending = False
            self._last = now
            self.fires += 1
        self._do_fire()
        return True


# ---------------------------------------------------------------------------
# Auto-continue trigger
# ---------------------------------------------------------------------------

AUTO_CONTINUE_PROMPT = ("Subagent results have arrived. Continue the task "
                        "using them.")


class AutoContinueGate:
    """Decide when finished-swarm results should start a follow-up turn.

    Fires once per batch of results: pending results must have been
    waiting for ``debounce`` seconds with no newer worker event (so several
    workers finishing together give ONE turn), the main agent must be idle,
    the prompt empty and the feature enabled. A result set that already
    triggered a turn but was not consumed (same fingerprint) never fires
    again — no follow-up loop if the agent cannot take the results.
    """

    def __init__(self, debounce: float = 0.8):
        self.debounce = float(debounce)
        self._lock = threading.Lock()
        self._since: Optional[float] = None
        self._last_event = -1e18
        self._last_fp: Any = None
        self.fired = 0

    def note_event(self, now: float) -> None:
        with self._lock:
            self._last_event = now

    def check(self, now: float, *, pending: int, fingerprint: Any = None,
              idle: bool, buffer_empty: bool, enabled: bool = True) -> bool:
        with self._lock:
            if not enabled or pending <= 0:
                self._since = None
                if pending <= 0:
                    self._last_fp = None
                return False
            if self._since is None:
                self._since = now
            if not idle or not buffer_empty:
                return False
            fp = fingerprint if fingerprint is not None else ("n", pending)
            if fp == self._last_fp:
                return False
            if now - max(self._since, self._last_event) < self.debounce:
                return False
            self._last_fp = fp
            self._since = None
            self.fired += 1
            return True

    def waiting(self) -> bool:
        with self._lock:
            return self._since is not None


def results_fingerprint(snapshot: Any, pending: int) -> Any:
    """Identity of the waiting result set (ids of undelivered finished
    workers when the snapshot reports ``delivered``, else the count)."""
    workers = _workers(snapshot)
    if workers and all("delivered" in w for w in workers):
        ids = frozenset(wid(w) for w in workers
                        if worker_state(w) in FINISHED_STATES
                        and not w.get("delivered"))
        if ids:
            return ids
    return ("n", int(pending))
