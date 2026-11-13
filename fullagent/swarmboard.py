"""Live swarm dashboard for the FullAgent TUI.

The board sits above the prompt box while parallel subagents work. Two
pieces live here, both pure (no prompt_toolkit import, headless-testable):

``SwarmView``
    The board's *session governor*. A swarm session opens when the first
    worker goes live and closes a moment after the last one settles. While
    it is open the board keeps every member on screen (finished workers turn
    into ``✓`` rows instead of vanishing), rows stay in spawn order, and the
    board height never shrinks — the prompt box underneath cannot jump up
    and down as workers start, finish or swap places. The height changes at
    most when a new worker joins, and once when the session closes.

``render_board``
    Width-exact fragment rows: a header with live totals, one row per worker
    (state, role, name, smooth eighth-block progress bar, animated activity,
    tokens, run time) and an overall footer. Every row is clipped to the
    terminal width by display columns, so nothing ever wraps.

Every number on the board is real manager state from
``ParallelSubagents.snapshot()``: tokens are the provider-reported usage,
times are wall clock from the worker's real start, and progress is the
share of the worker's step budget it has used, eased for display (it never
moves backwards and only reads 100% once the worker has settled).
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cliui import (
    ACTIVE_STATES,
    STATE_COLOR_KEYS,
    STATE_ICONS,
    display_width,
    short_role,
    truncate,
)

Fragment = Tuple[str, str]
Row = List[Fragment]

TERMINAL = ("done", "blocked", "error", "cancelled")
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
EIGHTHS = " ▏▎▍▌▋▊▉█"
# Board keeps showing the finished swarm this long before it collapses,
# so the last ✓ is readable and the prompt does not jump the instant the
# final worker returns.
LINGER_S = 1.6
DEFAULT_MAX_ROWS = 8
# Cool → warm gradient for running bars (cyan → violet).
BAR_GRADIENT = ("#22d3ee", "#38bdf8", "#60a5fa", "#818cf8", "#a78bfa",
                "#c084fc")


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------

def compact_tokens(value: Any) -> str:
    try:
        n = max(0, int(value))
    except (TypeError, ValueError):
        n = 0
    if n < 1000:
        return str(n)
    if n < 100_000:
        return f"{n / 1000:.1f}k"
    if n < 1_000_000:
        return f"{n // 1000}k"
    return f"{n / 1_000_000:.1f}M"


def clock(seconds: Any) -> str:
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        s = 0
    if s >= 3600:
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def worker_tokens(row: dict) -> int:
    return int(_num(row.get("tokens_in")) + _num(row.get("tokens_out")))


def worker_seconds(row: dict, now: float) -> float:
    """Real running time: from the worker's start, frozen when it settles."""
    started = _num(row.get("started_at"))
    finished = _num(row.get("finished_at"))
    if started > 0:
        end = finished if finished > 0 else now
        return max(0.0, end - started)
    return _num(row.get("run_ms")) / 1000.0


def target_progress(row: dict, max_steps: int) -> float:
    """Progress in [0, 1] from the worker's real step budget usage.

    Settled workers are complete. A running worker reports the share of
    its step budget it has consumed, shaped so the first steps visibly
    move the bar; it is capped below 1 so only a settled worker reads 100%.
    """
    state = str(row.get("state") or "queued")
    if state in TERMINAL:
        return 1.0
    if state == "queued":
        return 0.0
    budget = max(1, int(_num(row.get("max_steps"), max_steps) or max_steps))
    steps = max(0.0, _num(row.get("steps")))
    tools = max(0.0, _num(row.get("tool_calls")))
    used = min(1.0, (steps + 0.25 * min(tools, 4 * max(1.0, steps))) / budget)
    shaped = 1.0 - (1.0 - used) ** 2.2
    return max(0.04, min(0.94, 0.04 + 0.9 * shaped))


def progress_bar(frac: float, cells: int, colors: dict,
                 state: str) -> Row:
    """Smooth eighth-block bar with a per-cell colour gradient."""
    cells = max(1, int(cells))
    frac = max(0.0, min(1.0, frac))
    total = frac * cells
    full = int(total)
    part = int(round((total - full) * 8))
    if part == 8:
        full, part = full + 1, 0
    full = min(full, cells)
    dim = colors.get("dim", "")
    if state == "done":
        fill_colors = [colors.get("green", "")] * cells
    elif state in ("error", "blocked"):
        fill_colors = [colors.get(STATE_COLOR_KEYS.get(state, "red"), "")] * cells
    elif state == "cancelled":
        fill_colors = [dim] * cells
    else:
        fill_colors = [BAR_GRADIENT[min(len(BAR_GRADIENT) - 1,
                                        i * len(BAR_GRADIENT) // cells)]
                       for i in range(cells)]
    frags: Row = [(dim, "▕")]
    for i in range(cells):
        if i < full:
            frags.append((fill_colors[i], "█"))
        elif i == full and part:
            frags.append((fill_colors[i], EIGHTHS[part]))
        else:
            frags.append((dim, "·"))
    frags.append((dim, "▏"))
    return frags


def _width(frags: Sequence[Fragment]) -> int:
    return sum(display_width(t) for _, t in frags)


def _clip_row(frags: Row, width: int) -> Row:
    """Hard-clip a fragment row to *width* display columns."""
    out: Row = []
    used = 0
    for style, text in frags:
        w = display_width(text)
        if used + w <= width:
            out.append((style, text))
            used += w
            continue
        room = width - used
        if room > 0:
            out.append((style, truncate(text, room)))
        break
    return out


def _fit(left: Row, right: Row, width: int, fill: str = " ",
         fill_style: str = "") -> Row:
    """``left … right`` padded with *fill* to exactly *width* columns."""
    lw, rw = _width(left), _width(right)
    if lw + rw + 1 > width:
        # right badge first to go, then the left side is clipped
        if rw and lw + 2 <= width:
            right = _clip_row(right, max(0, width - lw - 1))
            rw = _width(right)
        if lw + rw + 1 > width:
            return _clip_row(left, width)
    gap = width - lw - rw
    return left + [(fill_style, fill * gap)] + right


# ---------------------------------------------------------------------------
# session governor
# ---------------------------------------------------------------------------

class SwarmView:
    """Keeps the live board stable for the lifetime of one swarm session."""

    def __init__(self, linger_s: float = LINGER_S) -> None:
        self.linger_s = float(linger_s)
        self._members: Dict[str, int] = {}
        self._open = False
        self._opened_at = 0.0
        self._settled_at: Optional[float] = None
        self._height = 0
        self._shown: Dict[str, Tuple[float, float]] = {}
        self._seq = 0
        self.sessions = 0
        self.goal = ""
        self.last_summary: Optional[dict] = None
        self._last_rows: List[dict] = []

    # -- membership ------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._open

    def _open_session(self, now: float) -> None:
        self._open = True
        self._opened_at = now
        self._settled_at = None
        self._members.clear()
        self._shown.clear()
        self._height = 0
        self._seq = 0
        self.sessions += 1

    def _summarise(self, rows: List[dict], now: float) -> None:
        starts = [_num(r.get("started_at")) for r in rows
                  if _num(r.get("started_at")) > 0]
        ends = [_num(r.get("finished_at")) for r in rows
                if _num(r.get("finished_at")) > 0]
        self.last_summary = {
            "session": self.sessions,
            "total": len(rows),
            "done": sum(r.get("state") == "done" for r in rows),
            "failed": sum(r.get("state") in ("error", "blocked", "cancelled")
                          for r in rows),
            "tokens": sum(worker_tokens(r) for r in rows),
            "seconds": (max(ends) - min(starts)) if starts and ends else 0.0,
            "goal": self.goal,
        }

    def close(self) -> None:
        self._open = False
        self._members.clear()
        self._shown.clear()
        self._height = 0
        self._settled_at = None

    def update(self, snapshot: Optional[dict],
               now: Optional[float] = None) -> Optional[List[dict]]:
        """Fold a fresh snapshot in; return the session's rows or ``None``.

        ``None`` means the board is hidden (no swarm, or the linger after
        the last worker settled has expired).
        """
        now = time.time() if now is None else now
        workers = [w for w in ((snapshot or {}).get("workers") or [])
                   if isinstance(w, dict) and w.get("id")]
        live = [w for w in workers if w.get("state") in ACTIVE_STATES]
        if live:
            if not self._open:
                self._open_session(now)
            self._settled_at = None
            # Membership is per swarm (batch): a worker that started and
            # finished between two frames still belongs on the board with
            # its siblings. Older batches with nothing live stay history.
            live_batches = {str(w.get("batch_id")) for w in live
                            if w.get("batch_id")}
            for w in workers:
                wid = str(w["id"])
                if wid in self._members:
                    continue
                if (w.get("state") in ACTIVE_STATES
                        or str(w.get("batch_id") or "") in live_batches):
                    self._seq += 1
                    self._members[wid] = self._seq
        elif self._open:
            if self._settled_at is None:
                self._settled_at = now
            elif now - self._settled_at >= self.linger_s:
                self._summarise(self._last_rows, now)
                self.close()
        if not self._open:
            return None
        by_id = {str(w["id"]): w for w in workers}
        rows = [by_id[wid] for wid in sorted(self._members,
                                             key=self._members.get)
                if wid in by_id]
        if not rows:  # session reset under us (e.g. /new)
            self.close()
            return None
        self._last_rows = rows
        return rows

    @property
    def settling(self) -> bool:
        """True during the linger after every member settled."""
        return self._open and self._settled_at is not None

    def sticky_height(self, wanted: int) -> int:
        """Never shrink the board while the session is open."""
        if wanted > self._height:
            self._height = wanted
        return self._height

    def eased(self, wid: str, target: float, now: float) -> float:
        """Display progress that glides toward *target* and never regresses."""
        prev = self._shown.get(wid)
        if prev is None:
            value = target if target >= 1.0 else min(target, 0.04)
        else:
            shown, ts = prev
            if target >= 1.0:
                # settled: finish the glide quickly so ✓ reads 100%
                value = min(1.0, shown + max(0.25, (1.0 - shown)
                                             * min(1.0, (now - ts) * 8)))
            else:
                k = min(1.0, max(0.0, now - ts) * 3.0)
                value = max(shown, shown + (target - shown) * k)
        self._shown[wid] = (value, now)
        return value


# ---------------------------------------------------------------------------
# renderer
# ---------------------------------------------------------------------------

def _pick_visible(rows: List[dict], limit: int) -> List[dict]:
    """Choose which rows fit, keeping spawn order on screen.

    Live workers win slots over finished ones, so an 8-slot board on a
    12-worker swarm always shows the work that is still happening.
    """
    if len(rows) <= limit:
        return rows
    rank = {"running": 0, "cancelling": 1, "queued": 2}
    order = {id(r): i for i, r in enumerate(rows)}
    chosen = sorted(rows, key=lambda r: (rank.get(r.get("state"), 3),
                                         order[id(r)]))[:limit]
    keep = {id(r) for r in chosen}
    return [r for r in rows if id(r) in keep]


def _activity(row: dict) -> str:
    state = str(row.get("state") or "queued")
    if state in TERMINAL:
        text = row.get("summary") or row.get("error") or state
        first = " ".join(str(text).split())
        if state == "error" and row.get("error"):
            first = " ".join(str(row.get("error")).split())
        return first
    action = " ".join(str(row.get("current_action") or "").split())
    if state == "queued":
        if action.startswith("waiting on"):
            return action
        return "queued · waiting for a free slot"
    if (not action or action in ("starting", "running", "queued")
            or action.startswith("model step")):
        task = " ".join(str(row.get("task") or "").split())
        thinking = "thinking"
        if action.startswith("model step"):
            thinking = f"thinking ({action.split('model ', 1)[1]})"
        return f"{thinking} · {task}" if task else thinking
    return action


def render_board(
    rows: Optional[List[dict]],
    *,
    width: int,
    colors: dict,
    now: Optional[float] = None,
    view: Optional[SwarmView] = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_steps: int = 20,
    goal: str = "",
) -> List[Row]:
    """Fragment rows for the live swarm board (empty list = hidden)."""
    if not rows:
        return []
    now = time.time() if now is None else now
    width = max(24, int(width))
    view = view or SwarmView()
    c = colors
    dim = c.get("dim", "")

    counts: Dict[str, int] = {}
    for r in rows:
        st = str(r.get("state") or "queued")
        counts[st] = counts.get(st, 0) + 1
    total = len(rows)
    running = counts.get("running", 0)
    queued = counts.get("queued", 0)
    cancelling = counts.get("cancelling", 0)
    settled = sum(counts.get(s, 0) for s in TERMINAL)
    tokens = sum(worker_tokens(r) for r in rows)
    starts = [_num(r.get("started_at")) for r in rows
              if _num(r.get("started_at")) > 0]
    live = running + queued + cancelling
    if starts:
        ends = [_num(r.get("finished_at")) for r in rows]
        end = now if live else max([e for e in ends if e > 0] or [now])
        swarm_s = max(0.0, end - min(starts))
    else:
        swarm_s = 0.0

    # -- header ----------------------------------------------------------
    noun = "Agent" if total == 1 else "Agents"
    parts = []
    if running:
        parts.append(f"{running} running")
    if cancelling:
        parts.append(f"{cancelling} cancelling")
    if queued:
        parts.append(f"{queued} queued")
    done_n = counts.get("done", 0)
    if done_n:
        parts.append(f"{done_n} done")
    failed = sum(counts.get(s, 0) for s in ("error", "blocked", "cancelled"))
    if failed:
        parts.append(f"{failed} stopped")
    pulse = SPINNER[int(now * 10) % len(SPINNER)] if live else "◆"
    accent = f"bold {c.get('accent', '')}"
    base_left: Row = [
        (accent, f" {pulse} "),
        (accent, f"≡ {total} {noun} "),
        (f"bold {c.get('green', '')}", "(" + " · ".join(parts) + ")"),
    ]
    timer = (c.get("cyan", ""), f"⏱ {clock(swarm_s)}")
    toks = (c.get("pink", ""), f"Σ {compact_tokens(tokens)} tok")
    rights: List[Row] = [
        [(dim, " "), timer, (dim, " · "), toks, (dim, " · ↓ to manage ")],
        [(dim, " "), timer, (dim, " · "), toks, (dim, " ")],
        [(dim, " "), timer, (dim, " ")],
        [],
    ]
    compact = []
    for key, sym in (("running", "◉"), ("cancelling", "◌"), ("queued", "○"),
                     ("done", "✓")):
        if counts.get(key):
            compact.append(f"{counts[key]}{sym}")
    if failed:
        compact.append(f"{failed}×")
    compact_left: Row = base_left[:2] + [
        (f"bold {c.get('green', '')}", " ".join(compact))]
    header: Optional[Row] = None
    combos = [(base_left, r) for r in rights[:2]] + \
             [(compact_left, r) for r in rights[:3]] + [(compact_left, [])]
    for chosen_left, right in combos:
        room = width - _width(right) - 1
        if _width(chosen_left) > room:
            continue
        left = list(chosen_left)
        if goal:
            spare = room - _width(left) - 4
            if spare >= 12:
                left += [(dim, " · "), (c.get("fg", ""), truncate(goal, spare))]
        header = _fit(left, right, width, fill="─",
                      fill_style=c.get("border", dim))
        break
    if header is None:
        header = _clip_row(compact_left, width)
    out: List[Row] = [header]

    # -- worker rows ------------------------------------------------------
    limit = max(1, int(max_rows))
    visible = _pick_visible(rows, limit)
    wide = width >= 96
    medium = width >= 72
    name_w = 0
    if wide:
        name_w = min(18, max(display_width(str(r.get("name") or ""))
                             for r in visible))
    bar_cells = 12 if wide else (8 if medium else 5)
    chip_w = max(display_width(short_role(str(r.get("role") or "")))
                 for r in visible)
    chip_w = min(chip_w, 9)

    for idx, r in enumerate(visible):
        st = str(r.get("state") or "queued")
        color = c.get(STATE_COLOR_KEYS.get(st, "fg"), "")
        icon = STATE_ICONS.get(st, "·")
        frac = view.eased(str(r.get("id")), target_progress(r, max_steps),
                          now)
        pct = 100 if st in TERMINAL else min(99, int(frac * 100))
        chip = truncate(short_role(str(r.get("role") or "")), chip_w)
        left: Row = [
            (f"bold {color}", f" {icon} "),
            (f"bold {c.get('cyan', '')}",
             chip + " " * (chip_w - display_width(chip))),
            (dim, " "),
        ]
        if name_w:
            name = truncate(str(r.get("name") or r.get("id") or ""), name_w)
            left.append((c.get("fg", ""),
                         name + " " * (name_w - display_width(name)) + " "))
        left.extend(progress_bar(frac, bar_cells, c, st))
        left.append((f"bold {color}" if st in TERMINAL else c.get("fg", ""),
                     f" {pct:>3}% "))
        secs = worker_seconds(r, now)
        tail_bits: Row = []
        if medium:
            tail_bits.append((c.get("pink", ""),
                              f"{compact_tokens(worker_tokens(r)):>6} tok"))
            tail_bits.append((dim, " "))
        tail_bits.append((c.get("cyan", "") if st == "running" else dim,
                          f"{clock(secs) if st != 'queued' else '—':>5} "))
        if st == "running":
            spin = SPINNER[(int(now * 12) + idx * 3) % len(SPINNER)]
            act_prefix: Row = [(f"bold {c.get('accent', '')}", spin + " ")]
            act_style = c.get("fg", "")
        elif st in TERMINAL:
            act_prefix = [(f"bold {color}", "↳ ")]
            act_style = color if st != "done" else dim
        else:
            act_prefix = [(dim, "· ")]
            act_style = dim
        room = width - _width(left) - _width(tail_bits) - _width(act_prefix) - 1
        activity = truncate(_activity(r), max(0, room))
        mid: Row = act_prefix + [(act_style, activity)]
        line = left + mid
        out.append(_fit(line, tail_bits, width))

    # -- overall footer --------------------------------------------------
    shown_ids = {id(r) for r in visible}
    overall = (sum(view._shown.get(str(r.get("id")), (0.0, 0.0))[0]
                   for r in visible)
               + sum(1.0 if r.get("state") in TERMINAL else
                     target_progress(r, max_steps)
                     for r in rows if id(r) not in shown_ids)) / max(1, total)
    foot_left: Row = [(dim, "   overall ")]
    foot_left.extend(progress_bar(overall if live else 1.0,
                                  max(6, bar_cells + (6 if wide else 2)),
                                  c, "running" if live else "done"))
    foot_left.append((f"bold {c.get('fg', '')}",
                      f" {100 if not live else min(99, int(overall * 100)):>3}%"))
    foot_left.append((dim, f" · {settled}/{total} settled"))
    hidden = total - len(visible)
    if hidden > 0:
        foot_left.append((dim, f" · … +{hidden} more"))
    rate = tokens / swarm_s if swarm_s > 0.5 else 0.0
    foot_right: Row = []
    if live:
        foot_right.append((dim, f"{rate:,.0f} tok/s " if rate else ""))
    else:
        foot_right.append((f"bold {c.get('green', '')}",
                           "✓ swarm complete "))
    out.append(_fit(foot_left, foot_right, width))

    target_h = view.sticky_height(len(out))
    while len(out) < target_h:  # never shrink mid-session
        out.insert(len(out) - 1, [("", "")])
    return [_clip_row(row, width) for row in out]


def merge_snapshots(*snaps: Optional[dict]) -> dict:
    """Combine several manager snapshots (e.g. the real crew + /swarm demo).

    The first snapshot keeps its ids; later ones get an ``xN:`` prefix on
    worker and batch ids so two managers can never collide on the board.
    """
    merged: Dict[str, Any] = {"workers": [], "counts": {}, "max_steps": 20,
                              "batches": {}}
    for i, snap in enumerate(s for s in snaps if s):
        prefix = "" if i == 0 else f"x{i}:"
        steps = snap.get("max_steps", 20)
        for w in snap.get("workers") or []:
            if not isinstance(w, dict):
                continue
            row = dict(w)
            row["id"] = prefix + str(w.get("id"))
            row["batch_id"] = prefix + str(w.get("batch_id") or "")
            row.setdefault("max_steps", steps)
            merged["workers"].append(row)
        for k, v in (snap.get("counts") or {}).items():
            merged["counts"][k] = merged["counts"].get(k, 0) + int(v or 0)
        for bid, meta in (snap.get("batches") or {}).items():
            merged["batches"][prefix + str(bid)] = dict(meta or {})
        if i == 0:
            merged["max_steps"] = steps
            for key in ("max_active", "max_workers", "latest_batch_id",
                        "pending_notifications"):
                if key in snap:
                    merged[key] = snap[key]
    merged["active"] = sum(merged["counts"].get(s, 0) for s in ACTIVE_STATES)
    return merged


def session_goal(snapshot: Optional[dict], rows: Optional[List[dict]]) -> str:
    """The goal of the newest batch represented on the board."""
    if not snapshot or not rows:
        return ""
    batches = snapshot.get("batches") or {}
    for r in reversed(rows):
        goal = (batches.get(str(r.get("batch_id") or "")) or {}).get("goal")
        if goal:
            return str(goal)
    return ""
