"""Slash-command help catalog + smart follow-up suggestions.

Claude Code CLI parity feature: a categorized, aligned `/help` reference
and lightweight turn-end hints. No dependencies on tui.py (the wiring
goes the other way — tui.py imports this module), so the import is safe
anywhere.
"""

from __future__ import annotations


# COMMANDS: categorized command reference. Every entry is (command, one-line
# description). The set covers the real commands handled by tui.py
# `_route_slash` plus the commands introduced by this feature batch
# (/sessions, /compact, /output-style, /agents, /init, /review, /permissions).
COMMANDS: dict[str, list[tuple[str, str]]] = {
    "Session": [
        ("/new", "fresh conversation (saves the old one)"),
        ("/resume", "resume a previous session by id"),
        ("/sessions", "list saved sessions"),
        ("/save", "save current session to disk"),
        ("/compact", "compress context — keep working without losing state"),
        ("/clear", "clear the screen"),
        ("/history", "browse previous turns"),
        ("/rewind", "rewind timeline + files to an earlier event"),
        ("/revert", "revert files only (timeline untouched)"),
        ("/fork", "branch the timeline at an event"),
        ("/replay", "replay the session log as a film"),
    ],
    "Models": [
        ("/model", "switch model (browse with no arg)"),
        ("/models", "list all models and providers"),
        ("/effort", "reasoning effort: low → ultrahigh"),
        ("/output-style", "concise · detailed · code-only output style"),
    ],
    "Subagents": [
        ("/agents", "list / spawn / manage subagents (crew)"),
        ("/council", "run a review council over a proposal"),
        ("/debate", "multi-agent debate on a decision"),
    ],
    "Project": [
        ("/init", "scaffold a project structure"),
        ("/review", "review changed files since last turn"),
        ("/impact", "code blast-radius analysis"),
        ("/graph", "project dependency graph"),
        ("/coverage", "test coverage status"),
        ("/fuzz", "fuzzer status"),
        ("/judge", "deterministic check (exit_code, file_exists, …)"),
        ("/verify", "verify the Merkle event spine"),
        ("/goal", "set · prove · close · status · waive · clear a goal"),
    ],
    "System": [
        ("/help", "show this help"),
        ("/permissions", "view / toggle tool permissions"),
        ("/approve", "toggle auto-approve for tools"),
        ("/reasoning", "toggle reasoning display"),
        ("/usage", "token usage for this session"),
        ("/about", "version + provider list"),
        ("/exit", "quit (aliases: /quit, /q)"),
    ],
}

# Shorthand aliases. resolve_alias() normalizes any command string —
# canonical commands map to themselves.
ALIASES: dict[str, str] = {
    "/q": "/exit",
    "/quit": "/exit",
    "/h": "/help",
    "/?": "/help",
    "/m": "/model",
    "/n": "/new",
    "/c": "/clear",
    "/s": "/save",
    "/r": "/review",
    "/cmp": "/compact",
    "/ses": "/sessions",
    "/perm": "/permissions",
}


def resolve_alias(cmd: str) -> str:
    """Normalize a command string through the alias table."""
    cmd = cmd.strip()
    return ALIASES.get(cmd, cmd)


def format_help() -> str:
    """Return the full categorized help text, columns aligned."""
    width = max(len(cmd) for cmds in COMMANDS.values() for cmd, _ in cmds)
    lines: list[str] = []
    for category, cmds in COMMANDS.items():
        lines.append(f"{category}:")
        for cmd, desc in cmds:
            lines.append(f"  {cmd:<{width}}  {desc}")
        lines.append("")
    lines.append("Type /help <command> is not supported yet — ask a follow-up.")
    return "\n".join(lines).rstrip() + "\n"


# -- follow-up suggestions ---------------------------------------------------

_WRITE_TOOLS = {"write_file", "write", "edit_file", "apply_patch",
                "create_file", "patch"}
_SHELL_TOOLS = {"run_command", "run_shell", "live_shell", "bash", "sh"}


def build_turn_summary(turn) -> dict:
    """Collapse an agent Turn into the summary dict suggest_followups wants.

    Takes the real Turn object (agent.py) without importing it — duck-typed
    so help.py stays import-clean.
    """
    tools_used: list[str] = []
    files_changed: list[str] = []
    for ev in getattr(turn, "tools", []) or []:
        name = getattr(ev, "name", "") or ""
        if name and name not in tools_used:
            tools_used.append(name)
        args = getattr(ev, "args", {}) or {}
        for key in ("path", "file", "filename", "target"):
            val = args.get(key)
            if val and val not in files_changed:
                files_changed.append(str(val))
                break
    err = getattr(turn, "error", "") or ""
    return {
        "tools_used": tools_used,
        "had_error": bool(err) and err != "cancelled",
        "files_changed": files_changed,
    }


def suggest_followups(last_turn_summary: dict) -> list[str]:
    """Heuristic follow-up suggestions from a turn summary.

    summary = {"tools_used": [...], "had_error": bool,
               "files_changed": [...]}. Always returns 2-4 items, never empty.
    """
    tools_used = list(last_turn_summary.get("tools_used") or [])
    had_error = bool(last_turn_summary.get("had_error"))
    files_changed = list(last_turn_summary.get("files_changed") or [])

    suggestions: list[str] = []

    def add(s: str) -> None:
        if s not in suggestions:
            suggestions.append(s)

    if had_error:
        add("/review changed files")
        add("run tests")
        add("ask a follow-up")
    else:
        if files_changed:
            add("/review")
        if any(t in _WRITE_TOOLS for t in tools_used):
            add("run the test suite")
            add("/compact if context is large")
        elif any(t in _SHELL_TOOLS for t in tools_used):
            add("verify the command output")
        if tools_used and not _WRITE_TOOLS.intersection(tools_used):
            add("ask a follow-up")
        if not suggestions:
            add("ask a follow-up")
            add("/help")
        elif len(suggestions) < 2:
            add("/help")

    return suggestions[:4]


# -- self test ----------------------------------------------------------------

def _self_test() -> None:
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    for cat, cmds in COMMANDS.items():
        for cmd, desc in cmds:
            check(cmd.startswith("/"), f"{cat}: command {cmd!r} lacks '/'")
            check(bool(desc.strip()), f"{cat}: {cmd} has empty description")

    check(resolve_alias("/q") == "/exit", "alias /q -> /exit")
    check(resolve_alias("/h") == "/help", "alias /h -> /help")
    check(resolve_alias("/model") == "/model", "canonical maps to itself")
    check(resolve_alias(" /n ") == "/new", "alias trims whitespace")

    text = format_help()
    check("Session:" in text and "Models:" in text and "Subagents:" in text
          and "Project:" in text and "System:" in text,
          "format_help has all categories")
    check("/exit" in text and "/review" in text, "format_help lists commands")

    err_case = suggest_followups({"tools_used": ["run_command"],
                                  "had_error": True,
                                  "files_changed": ["a.py"]})
    check(2 <= len(err_case) <= 4 and err_case,
          f"error case suggestions: {err_case}")
    check("/review changed files" in err_case, "error case suggests review")

    write_case = suggest_followups({"tools_used": ["write_file"],
                                    "had_error": False,
                                    "files_changed": ["b.py"]})
    check(2 <= len(write_case) <= 4 and write_case,
          f"write case suggestions: {write_case}")
    check("/review" in write_case, "write case suggests /review")

    empty_case = suggest_followups({"tools_used": [], "had_error": False,
                                    "files_changed": []})
    check(2 <= len(empty_case) <= 4 and empty_case,
          f"empty case suggestions: {empty_case}")

    # turn-summary helper on a duck-typed fake
    class _Ev:
        def __init__(self, name, args):
            self.name = name
            self.args = args

    class _Turn:
        tools = [_Ev("write_file", {"path": "x.py"})]
        error = ""

    summ = build_turn_summary(_Turn())
    check(summ["tools_used"] == ["write_file"], "build_turn_summary tools")
    check(summ["files_changed"] == ["x.py"], "build_turn_summary files")
    check(summ["had_error"] is False, "build_turn_summary error flag")

    if failures:
        print("FAIL:")
        for f in failures:
            print(" -", f)
        raise SystemExit(1)
    print("PASS: help.py self-test (all checks green)")


if __name__ == "__main__":
    _self_test()
