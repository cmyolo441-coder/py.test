"""Model confusion detection — notice when tool-call behavior goes unfocused.

In the 512s incident the model's tool calls degraded into incoherent
repetition: the same failing call issued over and over with no adaptation.
This module is a lightweight, heuristic tripwire for exactly that pattern.

Public API:
    - :class:`ConfusionDetector` -- sliding-window (last 8 calls) scorer.
      ``note_call(name, args, ok, error)`` records a call; ``score()`` gives
      0..1; ``suggest()`` returns a reset suggestion once the score crosses
      the threshold (default 0.7); ``signals()`` exposes the raw components;
      ``reset()`` clears history.
    - module-level ``note_call()`` / ``suggest()`` -- convenience wrappers
      around a shared detector, for the coordinator to hook into later
      (hook wiring is deliberately left to the coordinator; this module
      does not touch ``agent.py``).

Signals (all computed over the last ``window`` calls):
    - error_rate: fraction of calls that failed.
    - repeat: how often the exact same call (tool + normalised args) recurs.
    - args_similarity: mean argument similarity across consecutive same-tool
      pairs (model re-issuing near-identical calls).
    - thrash: tool-churn — many distinct tools with no repeats (flailing
      across the toolbox instead of converging).

No ML — just weighted rules, tuned so a healthy sequence
(read -> edit -> run -> fix -> run) stays well under the threshold.

Weighting: ``score = 0.35*error_rate + 0.40*repeat + 0.25*args_similarity``
(+ 0.10*thrash as a tie-breaker). The 512s pattern (same failing call,
high error rate, near-identical args) lands at ~0.74; a healthy sequence
with one failure lands at ~0.35.
"""

from __future__ import annotations

import difflib
import json
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# tuning knobs
# ---------------------------------------------------------------------------

WINDOW = 8              # sliding window: last N tool calls
THRESHOLD = 0.7         # score at/above this triggers a suggestion
MIN_CALLS = 4           # need at least this many calls before judging
MAX_ARG_CHARS = 400     # args text truncated past this for hashing/similarity

W_ERROR = 0.35
W_REPEAT = 0.40
W_SIM = 0.25
W_THRASH = 0.10         # capped: total score never exceeds 1.0


def _norm_args(args: Any) -> str:
    """Normalise a tool-call args payload to a short comparable string."""
    if args is None:
        return ""
    if isinstance(args, str):
        text = args
    elif isinstance(args, dict):
        try:
            text = json.dumps(args, sort_keys=True, default=str)
        except Exception:
            text = repr(sorted((str(k), str(v)) for k, v in args.items()))
    else:
        try:
            text = json.dumps(args, default=str)
        except Exception:
            text = repr(args)
    if len(text) > MAX_ARG_CHARS:
        text = text[:MAX_ARG_CHARS] + "…"
    return text


def _similarity(a: str, b: str) -> float:
    """0..1 similarity of two normalised arg strings."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


class ConfusionDetector:
    """Sliding-window heuristic for unfocused / looping tool-call behaviour."""

    def __init__(self, window: int = WINDOW, threshold: float = THRESHOLD,
                 min_calls: int = MIN_CALLS) -> None:
        self.window = max(2, int(window))
        self.threshold = float(threshold)
        self.min_calls = max(2, int(min_calls))
        self._calls: Deque[Tuple[str, str, bool]] = deque(maxlen=self.window)
        # (tool_name, normalised_args, ok)
        self._armed = True  # suggest() fires once per crossing (no spam)

    # -- recording ---------------------------------------------------------
    def note_call(self, name: str, args: Any = None, ok: bool = True,
                  error: Optional[Any] = None) -> None:
        """Record one tool call. ``error`` set (or ``ok=False``) marks failure."""
        failed = (not ok) or (error is not None)
        self._calls.append((str(name), _norm_args(args), not failed))

    def reset(self) -> None:
        self._calls.clear()
        self._armed = True

    # -- signals -----------------------------------------------------------
    def signals(self) -> Dict[str, float]:
        """Raw 0..1 components of the score (empty dict when no data)."""
        n = len(self._calls)
        if n == 0:
            return {}
        errors = sum(1 for _, _, ok_ in self._calls if not ok_)
        error_rate = errors / n

        identical: Dict[Tuple[str, str], int] = {}
        for name_, argstr, _ in self._calls:
            key = (name_, argstr)
            identical[key] = identical.get(key, 0) + 1
        max_identical = max(identical.values())
        repeat = (max_identical - 1) / (n - 1) if n > 1 else 0.0

        sims: List[float] = []
        calls = list(self._calls)
        for (n1, a1, _), (n2, a2, _) in zip(calls, calls[1:]):
            if n1 == n2:
                sims.append(_similarity(a1, a2))
        args_sim = sum(sims) / len(sims) if sims else 0.0

        distinct = len({name_ for name_, _, _ in self._calls})
        churn = distinct / n
        thrash = max(0.0, (churn - 0.5) / 0.5) if n >= 5 else 0.0

        return {
            "error_rate": error_rate,
            "repeat": repeat,
            "args_similarity": args_sim,
            "thrash": thrash,
            "calls": float(n),
        }

    def score(self) -> float:
        """Combined confusion score, 0..1 (0 when not enough data)."""
        if len(self._calls) < self.min_calls:
            return 0.0
        s = self.signals()
        raw = (W_ERROR * s["error_rate"]
               + W_REPEAT * s["repeat"]
               + W_SIM * s["args_similarity"]
               + W_THRASH * s["thrash"])
        return max(0.0, min(1.0, raw))

    # -- suggestion --------------------------------------------------------
    def suggest(self) -> Optional[str]:
        """Return a reset suggestion when confusion crosses the threshold.

        Fires once per crossing (re-arms after the score drops back below
        the threshold) so the coordinator is not spammed every call.
        """
        sc = self.score()
        if sc < self.threshold:
            self._armed = True
            return None
        if not self._armed:
            return None
        self._armed = False

        s = self.signals()
        n = int(s.get("calls", 0))
        err_n = int(round(s["error_rate"] * n))
        rep_n = int(round(s["repeat"] * (n - 1))) + 1 if n > 1 else 1
        return (
            f"The last {n} tool calls look unfocused "
            f"({err_n} errors, {rep_n} identical repeat{'s' if rep_n != 1 else ''}, "
            f"confusion score {sc:.2f}). Consider: /retry with a cleaned "
            f"context, or restate the goal in one sentence."
        )


# ---------------------------------------------------------------------------
# shared instance + convenience wrappers (for the coordinator hook)
# ---------------------------------------------------------------------------

detector = ConfusionDetector()


def note_call(name: str, args: Any = None, ok: bool = True,
              error: Optional[Any] = None) -> None:
    """Record a tool call on the shared detector."""
    detector.note_call(name, args=args, ok=ok, error=error)


def suggest() -> Optional[str]:
    """Suggestion from the shared detector (None when all clear)."""
    return detector.suggest()


def reset() -> None:
    """Clear the shared detector's history."""
    detector.reset()


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    def check(name: str, cond: bool) -> None:
        if not cond:
            raise AssertionError(f"self-test FAILED: {name}")
        print(f"  ok: {name}")

    print("confusdetect self-test:")

    EDIT = {"path": "app.py", "old_string": "x = 1", "new_string": "x = 2"}

    # -- 1. the 512s pattern: same failing edit x5 mixed with random tools --
    d = ConfusionDetector()
    d.note_call("edit_file", EDIT, ok=False, error="old_string not found")
    d.note_call("edit_file", EDIT, ok=False, error="old_string not found")
    d.note_call("list_dir", {"path": "."}, ok=True)
    d.note_call("edit_file", EDIT, ok=False, error="old_string not found")
    d.note_call("edit_file", EDIT, ok=False, error="old_string not found")
    d.note_call("edit_file", EDIT, ok=False, error="old_string not found")
    d.note_call("grep", {"pattern": "x = 1"}, ok=True)
    d.note_call("run_command", {"cmd": "pytest"}, ok=False, error="failed")

    sc = d.score()
    sig = d.signals()
    print(f"  incoherent score={sc:.3f} signals={sig}")
    check("incoherent sequence scores >= 0.7", sc >= 0.7)

    msg = d.suggest()
    check("incoherent sequence triggers suggestion", msg is not None)
    check("suggestion mentions /retry", "/retry" in msg)
    check("suggestion mentions one-sentence restate",
          "one sentence" in msg)
    print(f"  suggestion: {msg}")

    # -- 2. no spam: second suggest() stays silent until re-arm --------------
    check("suggest() fires once per crossing", d.suggest() is None)

    # -- 3. re-arms after the model recovers --------------------------------
    d.note_call("read_file", {"path": "app.py"}, ok=True)
    d.note_call("edit_file", {"path": "app.py", "old_string": "y = 1",
                              "new_string": "y = 2"}, ok=True)
    d.note_call("run_command", {"cmd": "pytest -q"}, ok=True)
    d.note_call("run_command", {"cmd": "pytest -q full"}, ok=True)
    check("recovery drops score below threshold", d.score() < THRESHOLD)
    check("no suggestion after recovery", d.suggest() is None)
    check("re-armed after recovery", d._armed is True)

    # -- 4. healthy sequence: read -> edit -> run -> fix -> run --------------
    h = ConfusionDetector()
    h.note_call("read_file", {"path": "app.py"}, ok=True)
    h.note_call("edit_file", {"path": "app.py", "old_string": "x = 1",
                              "new_string": "x = 2"}, ok=True)
    h.note_call("run_command", {"cmd": "pytest -q"}, ok=True)
    h.note_call("edit_file", {"path": "app.py", "old_string": "x = 2",
                              "new_string": "x = 3"}, ok=True)
    h.note_call("run_command", {"cmd": "pytest -q"}, ok=True)
    print(f"  healthy score={h.score():.3f} signals={h.signals()}")
    check("healthy sequence does not trigger", h.suggest() is None)

    # -- 5. healthy with ONE failure + fix must not trigger ------------------
    h2 = ConfusionDetector()
    h2.note_call("read_file", {"path": "app.py"}, ok=True)
    h2.note_call("edit_file", {"path": "app.py", "old_string": "nope",
                               "new_string": "x = 2"},
                 ok=False, error="old_string not found")
    h2.note_call("read_file", {"path": "app.py"}, ok=True)
    h2.note_call("edit_file", {"path": "app.py", "old_string": "x = 1",
                               "new_string": "x = 2"}, ok=True)
    h2.note_call("run_command", {"cmd": "pytest -q"}, ok=True)
    print(f"  healthy-with-failure score={h2.score():.3f} "
          f"signals={h2.signals()}")
    check("single failure + fix does not trigger", h2.suggest() is None)

    # -- 6. not enough data yet: no judgement --------------------------------
    t = ConfusionDetector()
    t.note_call("edit_file", EDIT, ok=False, error="boom")
    t.note_call("edit_file", EDIT, ok=False, error="boom")
    check("below min_calls scores 0", t.score() == 0.0)
    check("below min_calls no suggestion", t.suggest() is None)

    # -- 7. pure loop: 8 identical failing calls -> max score ----------------
    loop = ConfusionDetector()
    for _ in range(8):
        loop.note_call("read_file", {"path": "missing.py"},
                       ok=False, error="not found")
    check("pure identical-failure loop maxes score", loop.score() == 1.0)
    check("pure loop triggers", loop.suggest() is not None)

    # -- 8. adapting retries (args change each time) stay under threshold ----
    adapt = ConfusionDetector()
    for i in range(6):
        adapt.note_call("edit_file",
                        {"path": "app.py",
                         "old_string": f"candidate_{i}",
                         "new_string": "x = 2"},
                        ok=False, error="old_string not found")
    adapt.note_call("read_file", {"path": "app.py"}, ok=True)
    adapt.note_call("run_command", {"cmd": "true"}, ok=True)
    print(f"  adapting score={adapt.score():.3f} signals={adapt.signals()}")
    check("adapting retries do not trigger", adapt.suggest() is None)

    # -- 9. shared module-level API ------------------------------------------
    reset()
    note_call("read_file", {"path": "a.py"}, ok=True)
    check("module-level note_call records", len(detector._calls) == 1)
    check("module-level suggest all-clear", suggest() is None)
    reset()

    print("confusdetect self-test: ALL PASSED")
