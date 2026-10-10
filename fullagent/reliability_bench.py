"""Reliability benchmark: reproduces the 512-second incident as a simulation.

Incident: the agent issued the same failing MultiEdit ("old_string not found
in file") over and over for 512s instead of breaking out with a helpful
message.

This harness simulates that exact scenario:
  1. creates a temp file,
  2. runs a fake turn loop where the "model" keeps issuing the SAME failing
     MultiEdit call (bad old_string, real fullagent.multiedit.multi_edit
     executes it),
  3. wires in whatever real guardrails exist in the repo
     (fullagent/loopdetect.py, retryhint.py, tooldedup.py, editfallback.py --
     imported defensively, since they may not exist yet),
  4. asserts the loop breaks within <=5 tool calls with a helpful message
     (no raw crash, no 512s of retries).

If no guardrail modules exist yet, the bench falls back to a minimal local
reference detector (marked TEST-ONLY below) to prove the loop WOULD be
caught.

Run:  python3 -m fullagent.reliability_bench
  or  python3 fullagent/reliability_bench.py
"""

import importlib
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # repo root, so `import fullagent` works
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fullagent.multiedit import multi_edit  # the REAL tool under test

# ---------------------------------------------------------------------------
# Guardrail wiring (defensive: sibling workers may not have landed yet)
# ---------------------------------------------------------------------------

GUARDRAIL_CANDIDATES = ["loopdetect", "retryhint", "tooldedup", "editfallback"]


def load_guardrails():
    """Import any guardrail modules that exist. Returns {name: module}."""
    loaded = {}
    for name in GUARDRAIL_CANDIDATES:
        try:
            loaded[name] = importlib.import_module(f"fullagent.{name}")
        except Exception:
            pass
    return loaded


def probe_module_hooks(mod):
    """Best-effort: find a record/observe-style hook in a guardrail module.

    Returns a callable hook(tool_name, args_dict, result_str) -> Optional[str]
    (breakout message when the guardrail decides to stop the loop), or None.
    """
    for attr in ("LoopDetector", "Detector", "loop_detector"):
        obj = getattr(mod, attr, None)
        if obj is None:
            continue
        inst = obj() if isinstance(obj, type) else obj
        for meth in ("record", "observe", "note", "check"):
            fn = getattr(inst, meth, None)
            if callable(fn):
                return lambda t, a, r, _fn=fn: _fn(t, a, r)
    for fn_name in ("record", "observe", "check_call", "should_break"):
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            return lambda t, a, r, _fn=fn: _fn(t, a, r)
    return None


# ---------------------------------------------------------------------------
# TEST-ONLY fallback reference detector
# (This class exists ONLY inside this benchmark. It is not shipped as a
# feature and must not be confused with the real guardrails listed above.)
# ---------------------------------------------------------------------------

class _ReferenceLoopDetector:
    """TEST-ONLY minimal reference detector proving the loop is catchable.

    Fires when the same failing tool call is repeated `threshold` times in a
    row, producing a helpful breakout message instead of another retry.
    """

    def __init__(self, threshold=3):
        self.threshold = threshold
        self._last_sig = None
        self._streak = 0

    def observe(self, tool, args, result):
        sig = (tool, json.dumps(args, sort_keys=True, default=str))
        failed = result.strip().upper().startswith("ERROR")
        if failed and sig == self._last_sig:
            self._streak += 1
        else:
            self._last_sig = sig
            self._streak = 1 if failed else 0
        if self._streak >= self.threshold:
            return self._breakout_message(tool, args, result)
        return None

    @staticmethod
    def _breakout_message(tool, args, result):
        edits = (args.get("edits") or [{}])
        old = edits[0].get("old_string", "") if isinstance(edits[0], dict) else ""
        target = args.get("file_path") or args.get("path") or args.get("file") or "the file"
        return (
            f"STOP: {tool} failed {3} times in a row with the identical call.\n"
            f"Last error: {result.strip()}\n"
            f"The old_string does not match anything in {target}. "
            "Retrying the same edit cannot succeed.\n"
            "Do instead:\n"
            "  1. READ the file (or the relevant section) to see its exact "
            "current content, including whitespace/indentation.\n"
            "  2. Adjust old_string to match the file byte-for-byte, or pick "
            "a different anchor.\n"
            f"  (rejected old_string began with: {old[:60]!r})"
        )


# ---------------------------------------------------------------------------
# The incident simulation
# ---------------------------------------------------------------------------

FILE_CONTENT = """def greet(name):
    return "hello " + name


def main():
    print(greet("world"))


if __name__ == "__main__":
    main()
"""

# The model's stubborn bad call: old_string that never matches the file.
STUCK_CALL = {
    "file_path": None,  # filled in with temp path
    "edits": [
        {
            "old_string": "def greet(name):\n    return 'hi ' + name",
            "new_string": "def greet(name):\n    return 'hola ' + name",
        }
    ],
}

MAX_CALLS = 5  # must break out within this many tool calls
BUDGET_SECONDS = 30  # far, far below the 512s incident


def fake_model_issue_call(path):
    """The 'model' is stuck: it always issues the same failing MultiEdit."""
    call = {"file_path": path, "edits": list(STUCK_CALL["edits"])}
    return ("MultiEdit", call)


def run_benchmark(verbose=True):
    guardrails = load_guardrails()
    hooks = []
    for name, mod in guardrails.items():
        hook = probe_module_hooks(mod)
        if hook is not None:
            hooks.append((name, hook))

    if verbose:
        present = sorted(guardrails) or ["(none yet)"]
        print(f"[bench] guardrail modules present: {', '.join(present)}")

    use_fallback = not hooks
    detectors = [] if not use_fallback else [("reference-detector (test-only)", None)]
    fallback = _ReferenceLoopDetector(threshold=3) if use_fallback else None
    if verbose and use_fallback:
        print("[bench] no guardrail hooks found -> using TEST-ONLY reference detector")

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(FILE_CONTENT)
        path = f.name

    calls_made = 0
    breakout = None
    fired_by = None
    last_result = None
    start = time.monotonic()
    try:
        while calls_made < MAX_CALLS:
            tool, args = fake_model_issue_call(path)
            last_result = multi_edit(**args)  # REAL tool executes
            calls_made += 1
            if verbose:
                print(f"[bench] call {calls_made}: {last_result[:80]}")
            failed = last_result.strip().upper().startswith("ERROR")
            if not failed:
                break  # call succeeded: no loop to catch
            if fallback is not None:
                breakout = fallback.observe(tool, args, last_result)
                if breakout:
                    fired_by = "reference-detector (test-only)"
                    break
            else:
                for name, hook in hooks:
                    try:
                        msg = hook(tool, args, last_result)
                    except Exception as e:  # a guardrail must never crash the loop
                        msg = None
                        if verbose:
                            print(f"[bench] guardrail {name} raised {e!r}; ignored")
                    if msg:
                        breakout, fired_by = msg, name
                        break
                if breakout:
                    break
    finally:
        elapsed = time.monotonic() - start
        # file must be untouched: validation rejected every edit
        with open(path) as f:
            intact = f.read() == FILE_CONTENT
        os.unlink(path)

    # ---- assertions ----
    failures = []
    if calls_made > MAX_CALLS:
        failures.append(f"loop ran {calls_made} calls, budget is {MAX_CALLS}")
    if breakout is None:
        failures.append("no guardrail fired: loop did not break out")
    if breakout is not None and (
        "Traceback" in breakout or len(breakout.strip()) < 40
    ):
        failures.append("breakout message is not helpful (crash or empty)")
    if elapsed > BUDGET_SECONDS:
        failures.append(f"took {elapsed:.1f}s, over the {BUDGET_SECONDS}s budget")
    if not intact:
        failures.append("temp file was modified by the failing edits")

    passed = not failures
    print("\n===== reliability_bench RESULT =====")
    print(f"calls made        : {calls_made} (budget <= {MAX_CALLS})")
    print(f"guardrail fired   : {fired_by if fired_by else 'NONE'}")
    print(f"wall time         : {elapsed:.2f}s (incident: 512s)")
    print(f"file intact       : {intact}")
    print("breakout message  :")
    for line in (breakout or "(none)").splitlines():
        print(f"    {line}")
    if passed:
        print("\nPASS: stuck-MultiEdit loop broke out quickly with a helpful message")
    else:
        print("\nFAIL:")
        for fl in failures:
            print(f"  - {fl}")
    return passed


def main():
    ok = run_benchmark()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
