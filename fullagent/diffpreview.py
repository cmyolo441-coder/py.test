"""Diff preview — confirm before applying file changes.

Claude Code-style confirm gate for mutating tools: before ``write_file``,
``edit_file``, ``apply_patch`` or ``MultiEdit`` runs, the caller can show a
unified diff of exactly what will change and ask the user to confirm.

Public API:
    - :func:`build_diff` -- unified diff text for a mutating tool call.
    - :func:`preview_and_confirm` -- show the diff, ask y/n, return bool.
    - :func:`register` -- attach ``agent.diff_preview`` (bound) for the
      coordinator to call from ``Agent._execute_tool``.

Design notes (from the existing code base):
    - ``permissions.py`` (round 1) defines the four modes
      ``default / plan / acceptEdits / bypassPermissions`` and stores the
      manager on ``agent.permissions``. ``bypassPermissions`` skips the
      preview entirely; plan mode always previews.
    - The TUI already has its own lightweight ``_diff_preview`` inside
      ``tui.py::_approve_blocking``; this module is the coordinator-level
      gate that works with any ``ui`` (TUI, headless, or tests).
    - Tool signatures (from ``tools.py`` / ``multiedit.py``):
      ``write_file(path, content)``,
      ``edit_file(path, old_string, new_string, replace_all=False)``,
      ``apply_patch(patch)``,
      ``MultiEdit(file_path, edits=[{old_string, new_string}, ...])``.
"""

from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Mutating tools this gate knows how to diff.
DIFF_TOOLS = ("write_file", "edit_file", "apply_patch", "MultiEdit",
              "multiedit")

# Diffs longer than this are truncated with a note.
MAX_DIFF_LINES = 200

_DIFF_CONTEXT = 3


def _read_current(path_str: Any) -> Optional[str]:
    """Read the file as it stands on disk; None when unreadable."""
    try:
        p = Path(str(path_str)).expanduser()
    except Exception:
        return None
    try:
        if p.is_dir():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _unified(old: str, new: str, path: str) -> str:
    """Unified diff of old -> new, capped at MAX_DIFF_LINES lines."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="a/" + path if old else "/dev/null",
        tofile="b/" + path,
        lineterm="", n=_DIFF_CONTEXT))
    if not diff:
        return ""
    if len(diff) > MAX_DIFF_LINES:
        extra = len(diff) - MAX_DIFF_LINES
        diff = diff[:MAX_DIFF_LINES]
        diff.append(f"... [truncated: {extra} more diff line(s)]")
    return "\n".join(diff)


def _summarize(path: str, is_new: bool) -> str:
    if is_new:
        return f"--- {path} (new file)"
    return f"--- {path}"


# ---------------------------------------------------------------------------
# Diff builders per tool
# ---------------------------------------------------------------------------

def _diff_write_file(args: Dict[str, Any]) -> str:
    path = str(args.get("path", ""))
    content = args.get("content", "")
    if not isinstance(content, str):
        return f"--- {path}\n(cannot preview: content is not a string)"
    old = _read_current(path)
    if old is None:
        old = ""  # new file (or unreadable) — diff against empty
    new = content
    return _summarize(path, old == "") + "\n" + (
        _unified(old, new, path) or "(no changes — content matches the file)")


def _diff_edit_file(args: Dict[str, Any]) -> str:
    path = str(args.get("path", ""))
    old_s = args.get("old_string", "")
    new_s = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    old = _read_current(path)
    if old is None:
        return f"--- {path}\n(cannot preview: file not readable)"
    if not isinstance(old_s, str) or old_s == "":
        return f"--- {path}\n(cannot preview: old_string is empty)"
    if old_s not in old:
        return (f"--- {path}\n(cannot preview: old_string NOT FOUND in "
                "file — this edit will fail)")
    new = old.replace(old_s, str(new_s),
                      -1 if replace_all else 1)
    return _summarize(path, False) + "\n" + (
        _unified(old, new, path) or "(no changes)")


def _diff_multiedit(args: Dict[str, Any]) -> str:
    path = str(args.get("file_path", ""))
    edits = args.get("edits", [])
    old = _read_current(path)
    if old is None:
        return f"--- {path}\n(cannot preview: file not readable)"
    if not isinstance(edits, list) or not edits:
        return f"--- {path}\n(cannot preview: edits list is empty)"
    new = old
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return (f"--- {path}\n(cannot preview: edit #{i} is not a "
                    "dict)")
        old_s = edit.get("old_string")
        new_s = edit.get("new_string")
        if not isinstance(old_s, str) or not old_s:
            return (f"--- {path}\n(cannot preview: edit #{i} has no "
                    "old_string)")
        if not isinstance(new_s, str):
            return (f"--- {path}\n(cannot preview: edit #{i} has no "
                    "new_string)")
        if old_s not in new:
            return (f"--- {path}\n(cannot preview: edit #{i} old_string "
                    "not found — this MultiEdit will fail)")
        new = new.replace(old_s, new_s, 1)
    return _summarize(path, False) + "\n" + (
        _unified(old, new, path) or "(no changes)")


def _diff_apply_patch(args: Dict[str, Any]) -> str:
    patch = args.get("patch", "")
    if not isinstance(patch, str) or not patch.strip():
        return "(cannot preview: patch is empty)"
    lines = patch.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        extra = len(lines) - MAX_DIFF_LINES
        lines = (lines[:MAX_DIFF_LINES]
                 + [f"... [truncated: {extra} more patch line(s)]"])
    return "\n".join(lines)


_BUILDERS: Dict[str, Callable[[Dict[str, Any]], str]] = {
    "write_file": _diff_write_file,
    "edit_file": _diff_edit_file,
    "MultiEdit": _diff_multiedit,
    "multiedit": _diff_multiedit,
    "apply_patch": _diff_apply_patch,
}


def build_diff(tool_name: str, args: Dict[str, Any]) -> str:
    """Build a unified diff for a mutating tool call.

    Unknown tools return a short note (never raise). Huge diffs are
    truncated to :data:`MAX_DIFF_LINES` lines with a trailing note.
    """
    builder = _BUILDERS.get(str(tool_name))
    if builder is None:
        return f"(no diff preview available for tool {tool_name!r})"
    try:
        return builder(args or {})
    except Exception as e:  # never break the turn on a preview bug
        return f"(diff preview failed: {e})"


# ---------------------------------------------------------------------------
# UI plumbing (duck-typed — TUI, headless, or tests)
# ---------------------------------------------------------------------------

def _print_diff(diff: str, tool_name: str, ui: Any) -> None:
    header = f"Diff preview — {tool_name}:"
    body = header + "\n" + diff
    try:
        if ui is None:
            print(body)
        elif hasattr(ui, "print_info"):
            ui.print_info(body)
        elif hasattr(ui, "console"):  # TUI — rich console
            ui.console.print(body)
        elif hasattr(ui, "print"):    # duck-typed printers
            ui.print(body)
        else:
            print(body)
    except Exception:
        print(body)  # printing must never fail the gate


def _ask_yes_no(prompt: str, ui: Any) -> bool:
    """Ask y/n; True = proceed. Non-tty without a ui asker: proceed.

    In headless/CI there is nobody to ask — blocking every file write
    would silently break automation, so the gate is transparent there
    and the diff was already logged to stdout.
    """
    answer = None
    try:
        ask = (getattr(ui, "ask_yes_no", None)
               or getattr(ui, "confirm", None))
        if ask is not None:
            answer = ask(prompt)
        elif sys.stdin.isatty():
            try:
                answer = input(prompt + " ")
            except (EOFError, KeyboardInterrupt):
                return False
        else:
            return True
    except Exception:
        return True
    if answer is None:
        return True
    a = str(answer).strip().lower()
    return a in ("", "y", "yes")


def _preview_path(tool_name: str, args: Dict[str, Any]) -> str:
    if str(tool_name) in ("write_file", "edit_file"):
        return str(args.get("path", "")).strip()
    if str(tool_name) in ("MultiEdit", "multiedit"):
        return str(args.get("file_path", "")).strip()
    return ""


def preview_and_confirm(tool_name: str, args: Dict[str, Any],
                        ui: Any = None) -> bool:
    """Print the diff via ``ui`` and ask the user to confirm.

    Returns True to proceed with the tool call, False to abort it.
    """
    diff = build_diff(tool_name, args or {})
    _print_diff(diff, tool_name, ui)
    path = _preview_path(tool_name, args or {})
    if path:
        prompt = f"Apply changes to {path}? [Y/n]"
    else:
        prompt = f"Apply {tool_name}? [Y/n]"
    return _ask_yes_no(prompt, ui)


# ---------------------------------------------------------------------------
# Permission-mode respect + registration
# ---------------------------------------------------------------------------

def _permission_mode(agent: Any) -> Optional[str]:
    """Current permission mode via getattr guards; None when unknown.

    ``permissions.register(agent)`` (round 1) stores the manager on
    ``agent.permissions`` with a ``.mode`` property.
    """
    pm = getattr(agent, "permissions", None)
    if pm is None:
        pm = getattr(agent, "perms", None)
    if pm is None:
        pm = getattr(agent, "permission_manager", None)
    if pm is None:
        return None
    mode = getattr(pm, "mode", None)
    try:
        return str(mode() if callable(mode) else mode)
    except Exception:
        return None


def register(agent: Any) -> None:
    """Attach ``agent.diff_preview(tool_name, args, ui=None)``.

    The coordinator calls it from ``Agent._execute_tool`` before running
    a mutating tool. ``bypassPermissions`` skips the preview entirely;
    plan mode (and everything else) always previews.
    """

    def _bound(tool_name: str, args: Dict[str, Any],
               ui: Any = None) -> bool:
        if _permission_mode(agent) == "bypassPermissions":
            return True
        return preview_and_confirm(tool_name, args, ui)

    agent.diff_preview = _bound


# ---------------------------------------------------------------------------
# Insertion point for the coordinator (do NOT edit agent.py from here):
#
#   fullagent/agent.py :: Agent._execute_tool, right before the tool is
#   actually invoked — i.e. after the "needs_ask" approval block and the
#   A2/I3 snapshot block, just before `on_status(f"running:{ev.name}")`.
#   Suggested snippet:
#
#       # Feature: diff preview gate — confirm before applying changes
#       if ev.name in ("write_file", "edit_file", "apply_patch",
#                      "MultiEdit", "multiedit"):
#           preview = getattr(self, "diff_preview", None)
#           ui = getattr(self, "ui", None)
#           if callable(preview) and not preview(ev.name, ev.args, ui):
#               ev.status = "denied"
#               ev.result = ("ERROR: user declined the diff preview. "
#                            "Ask the user how to proceed or choose "
#                            "another approach.")
#               return
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import shutil
    import tempfile

    def check(name: str, cond: bool) -> None:
        if not cond:
            raise AssertionError(f"self-test FAILED: {name}")

    tmp = tempfile.mkdtemp(prefix="diffpreview_selftest_")
    os.chdir(tmp)

    # -- fake ui + fake agent ---------------------------------------------
    class FakeUI:
        def __init__(self, answers=()):
            self.printed = []
            self.answers = list(answers)

        def print_info(self, text):
            self.printed.append(text)

        def confirm(self, prompt):
            self.printed.append("PROMPT: " + prompt)
            return self.answers.pop(0) if self.answers else "y"

    class FakePerms:
        def __init__(self, mode):
            self._m = mode

        @property
        def mode(self):
            return self._m

    class FakeAgent:
        def __init__(self, mode=None):
            self.permissions = FakePerms(mode) if mode else None

    # -- build_diff: write_file on a new file ------------------------------
    d = build_diff("write_file", {"path": "new.txt",
                                  "content": "hello\nworld\n"})
    check("write_new diff has plus lines",
          "+hello" in d and "+world" in d)
    check("write_new labels new file", "/dev/null" in d)

    # -- build_diff: write_file overwrite ----------------------------------
    Path("old.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    d = build_diff("write_file", {"path": "old.txt",
                                  "content": "alpha\nGAMMA\n"})
    check("write_overwrite minus line", "-beta" in d)
    check("write_overwrite plus line", "+GAMMA" in d)
    check("write_overwrite context", "alpha" in d)

    # -- build_diff: write_file with identical content ----------------------
    d = build_diff("write_file", {"path": "old.txt",
                                  "content": "alpha\nbeta\n"})
    check("write_identical reports no changes", "no changes" in d)

    # -- build_diff: edit_file ------------------------------------------------
    d = build_diff("edit_file", {"path": "old.txt",
                                 "old_string": "beta\n",
                                 "new_string": "BETA!\n"})
    check("edit minus/plus", "-beta" in d and "+BETA!" in d)

    # -- build_diff: edit_file old_string missing -----------------------------
    d = build_diff("edit_file", {"path": "old.txt",
                                 "old_string": "nope", "new_string": "x"})
    check("edit_missing warns", "NOT FOUND" in d)

    # -- build_diff: edit_file unreadable --------------------------------------
    d = build_diff("edit_file", {"path": "missing.txt",
                                 "old_string": "a", "new_string": "b"})
    check("edit_unreadable note", "not readable" in d)

    # -- build_diff: MultiEdit --------------------------------------------------
    d = build_diff("MultiEdit", {"file_path": "old.txt", "edits": [
        {"old_string": "alpha", "new_string": "ALPHA"},
        {"old_string": "beta", "new_string": "BETA"}]})
    check("multiedit diff", "-alpha" in d and "+ALPHA" in d
          and "-beta" in d and "+BETA" in d)

    # -- build_diff: MultiEdit bad edit -----------------------------------------
    d = build_diff("MultiEdit", {"file_path": "old.txt", "edits": [
        {"old_string": "missing", "new_string": "x"}]})
    check("multiedit_bad warns", "not found" in d)

    # -- build_diff: apply_patch passes the patch through --------------------------
    patch = ("--- a/old.txt\n+++ b/old.txt\n@@ -1,2 +1,2 @@\n"
             " alpha\n-beta\n+BETA\n")
    d = build_diff("apply_patch", {"patch": patch})
    check("apply_patch passthrough", "-beta" in d and "+BETA" in d)

    # -- truncation --------------------------------------------------------------
    big = "".join(f"line{i}\n" for i in range(500))
    d = build_diff("write_file", {"path": "big.txt", "content": big})
    lines = d.splitlines()
    check("truncation caps lines",
          len(lines) <= MAX_DIFF_LINES + 3)
    check("truncation note present", "truncated" in d)

    # -- unknown tool never raises ---------------------------------------------------
    d = build_diff("run_command", {"command": "ls"})
    check("unknown tool note", "no diff preview" in d)

    # -- preview_and_confirm: yes --------------------------------------------------------
    ui = FakeUI(answers=["y"])
    ok = preview_and_confirm("edit_file", {"path": "old.txt",
                                           "old_string": "beta\n",
                                           "new_string": "BETA!\n"}, ui)
    check("confirm yes proceeds", ok is True)
    check("diff printed via ui", any("-beta" in p for p in ui.printed))
    check("prompt mentions path", any("old.txt" in p for p in ui.printed))

    # -- preview_and_confirm: no ----------------------------------------------------------
    ui = FakeUI(answers=["n"])
    ok = preview_and_confirm("write_file",
                             {"path": "new2.txt", "content": "x\n"}, ui)
    check("confirm no aborts", ok is False)

    # -- preview_and_confirm: empty answer defaults to yes ----------------------------------
    ui = FakeUI(answers=[""])
    ok = preview_and_confirm("write_file",
                             {"path": "new2.txt", "content": "x\n"}, ui)
    check("empty answer proceeds", ok is True)

    # -- preview_and_confirm: plain stdout fallback (ui=None) ---------------------------------
    ok = preview_and_confirm("run_command", {"command": "x"}, None)
    check("non-tty fallback proceeds", ok is True)

    # -- register: bypassPermissions skips ---------------------------------------------------
    agent = FakeAgent("bypassPermissions")
    register(agent)
    check("diff_preview attached", callable(agent.diff_preview))
    ui = FakeUI()  # would answer, but must never be asked
    ok = agent.diff_preview("write_file",
                            {"path": "new2.txt", "content": "x\n"}, ui)
    check("bypass skips preview", ok is True and ui.printed == [])

    # -- register: plan mode always previews --------------------------------------------------
    agent = FakeAgent("plan")
    register(agent)
    ui = FakeUI(answers=["n"])
    ok = agent.diff_preview("edit_file", {"path": "old.txt",
                                          "old_string": "beta\n",
                                          "new_string": "BETA!\n"}, ui)
    check("plan mode previews and honors no", ok is False)

    # -- register: no permissions manager -> still previews -----------------------------------
    agent = FakeAgent(None)
    register(agent)
    ui = FakeUI(answers=["y"])
    ok = agent.diff_preview("write_file",
                            {"path": "new2.txt", "content": "x\n"}, ui)
    check("unknown mode previews", ok is True)

    shutil.rmtree(tmp, ignore_errors=True)
    print("diffpreview self-test PASSED")
