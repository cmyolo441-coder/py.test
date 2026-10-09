"""Custom subagent types — markdown-defined agents.

Users drop ``~/.fullagent/agents/*.md`` files; each one becomes a
spawnable subagent type usable by the Task tool (via the shared ROLES
table in team.py, which crew.spawn() validates against).

File format — YAML-ish frontmatter delimited by ``---`` lines::

    ---
    name: my-agent
    description: What this agent does
    tools: read_file, search_files, web_search
    ---
    The system prompt for this subagent type...

``tools:`` is optional — without it the agent gets the safe read-only
set below. A file with NO frontmatter uses the filename (sans ``.md``)
as the name and the whole file as the prompt.

Public API for the coordinator/TUI:
    - :func:`register` -- attach ``agent.custom_agent_types`` and merge
      the custom roles into ``team.ROLES`` (plus ``systemprompt.ROLE_BRIEFS``
      and the worker prompt registry — the exact pattern meta.py uses to
      seal a role). Duck-typed, no agent.py edits needed.
    - :func:`get_custom_roles` -- ``{name: {"description", "tools",
      "prompt"}}``, validated; malformed files are skipped gracefully.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from .tools import Tool

AGENTS_DIR = os.path.join(os.path.expanduser("~"), ".fullagent", "agents")

# Tools that grant write/execute power (mirrors meta.py's policy set — a
# role holding any of these is flagged writes=True downstream).
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "create_directory",
                          "delete_path", "move_path", "copy_path",
                          "run_command"})

# Safe read-only-ish default when a file names no tools (or none survive
# whitelist validation): matches the built-in reviewer's tool set.
DEFAULT_SAFE_TOOLS = ("read_file", "list_dir", "file_info", "search_files",
                      "glob_files")

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# frontmatter parsing
# ---------------------------------------------------------------------------

def _split_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    """Return (frontmatter dict, body). No frontmatter -> ({}, whole text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i, ln in enumerate(lines[1:], 1)
                   if ln.strip() == "---")
    except StopIteration:
        return {}, text  # unterminated --- ; treat whole file as prompt
    meta: Dict[str, str] = {}
    for ln in lines[1:end]:
        if ":" not in ln:
            continue
        key, _, value = ln.partition(":")
        meta[key.strip().lower()] = value.strip()
    return meta, "\n".join(lines[end + 1:])


def _parse_tool_list(raw: str) -> List[str]:
    return [t.strip() for t in re.split(r"[,\s]+", raw or "") if t.strip()]


# ---------------------------------------------------------------------------
# tool whitelist validation
# ---------------------------------------------------------------------------

_tool_names_cache: List[str] | None = None


def _all_tool_names() -> List[str]:
    """Every tool name a custom role may draw from. Cached."""
    global _tool_names_cache
    if _tool_names_cache is None:
        try:
            from .tools import build_registry
            _tool_names_cache = sorted(build_registry())
        except Exception:
            _tool_names_cache = []
    return _tool_names_cache


def _validate_tools(requested: List[str]) -> List[str]:
    """Intersect requested tools with registered ones.

    Unknown tools are dropped — never crash. Falls back to the safe
    read-only set when nothing valid remains.
    """
    known = set(_all_tool_names())
    tools = [t for t in requested if t in known]
    return tools if tools else list(DEFAULT_SAFE_TOOLS)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def get_custom_roles(agents_dir: str | None = None) -> Dict[str, Dict[str, Any]]:
    """Parse ``*.md`` files into custom role specs.

    Returns ``{name: {"description": ..., "tools": [...], "prompt": ...}}``.
    Malformed files (bad name, empty prompt, unreadable) are skipped
    gracefully — one bad file never breaks the load.
    """
    directory = agents_dir or AGENTS_DIR
    roles: Dict[str, Dict[str, Any]] = {}
    try:
        files = sorted(f for f in os.listdir(directory)
                       if f.endswith(".md"))
    except OSError:
        return roles
    for fname in files:
        path = os.path.join(directory, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue  # skip unreadable files gracefully
        meta, body = _split_frontmatter(text)
        prompt = body.strip()
        name = meta.get("name") or os.path.splitext(fname)[0]
        name = name.strip()
        if not _NAME_RE.match(name):
            continue  # invalid or missing name -> skip
        if not prompt:
            continue  # empty prompt -> skip
        description = (meta.get("description") or
                       f"Custom subagent type '{name}'").strip()
        tools = (_validate_tools(_parse_tool_list(meta["tools"]))
                 if "tools" in meta else list(DEFAULT_SAFE_TOOLS))
        if name in roles:
            continue  # first file wins; duplicates skipped
        roles[name] = {
            "description": description,
            "tools": tools,
            "prompt": prompt,
        }
    return roles


# ---------------------------------------------------------------------------
# registration — wire into the agent + crew
# ---------------------------------------------------------------------------

def _writes(tools: List[str]) -> bool:
    return bool(_WRITE_TOOLS & set(tools))


def register(agent: Any) -> None:
    """Attach custom agent types and merge them into the crew substrate.

    - ``agent.custom_agent_types``: the parsed role dict (for TUI/agent
      introspection).
    - ``team.ROLES[name] = {"tools": ..., "writes": ...}`` — crew.spawn()
      validates roles against ROLES, so after this merge the Task tool can
      spawn custom types with no agent.py change.
    - ``systemprompt.ROLE_BRIEFS[name]`` + prompt registry — same sealing
      pattern meta.py uses, so the worker system prompt resolves.
    """
    from .team import ROLES
    from . import systemprompt

    roles = get_custom_roles()
    agent.custom_agent_types = roles
    for name, spec in roles.items():
        if name in ROLES:
            continue  # built-in roles always win over custom ones
        tools = list(spec["tools"])
        ROLES[name] = {"tools": tuple(tools), "writes": _writes(tools)}
        brief = spec["description"]
        if spec["prompt"]:
            brief = f"{brief}\n\nCustom instructions:\n{spec['prompt']}"
        systemprompt.ROLE_BRIEFS[name] = brief
        systemprompt.register(f"worker:{name}",
                              systemprompt.worker(name, 8))


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "sql-expert.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("---\n"
                     "name: sql-expert\n"
                     "description: Database specialist\n"
                     "tools: read_file, search_files, run_command, fake_tool_xyz\n"
                     "---\n"
                     "You are a database specialist. Write efficient SQL.\n")
        with open(os.path.join(tmpdir, "plain.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("Just a plain file with no frontmatter at all.\n")
        with open(os.path.join(tmpdir, "bad.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("---\nname: bad name with spaces!\n---\nOops.\n")

        roles = get_custom_roles(tmpdir)
        assert set(roles) == {"sql-expert", "plain"}, roles
        # frontmatter parsed
        se = roles["sql-expert"]
        assert se["description"] == "Database specialist"
        assert se["prompt"] == "You are a database specialist. Write efficient SQL."
        # whitelist intersection: unknown tool dropped, never crashed
        assert "fake_tool_xyz" not in se["tools"], se["tools"]
        for t in ("read_file", "search_files", "run_command"):
            assert t in se["tools"], se["tools"]
        # no frontmatter: filename as name, default safe tools
        pl = roles["plain"]
        assert pl["prompt"] == "Just a plain file with no frontmatter at all."
        assert list(pl["tools"]) == list(DEFAULT_SAFE_TOOLS)
        # malformed name skipped gracefully
        assert "bad" not in roles and "bad name with spaces!" not in roles

        # register() attaches state + merges into ROLES (built-ins win)
        class FakeAgent:
            pass

        agent = FakeAgent()
        # point register() at the temp dir by patching the module constant
        _orig_dir = AGENTS_DIR
        globals()["AGENTS_DIR"] = tmpdir
        try:
            register(agent)
        finally:
            globals()["AGENTS_DIR"] = _orig_dir
        assert agent.custom_agent_types == roles
        from .team import ROLES
        assert "sql-expert" in ROLES
        assert ROLES["sql-expert"]["writes"] is True  # run_command
        assert "plain" in ROLES
        assert ROLES["plain"]["writes"] is False      # read-only set
        from . import systemprompt
        assert "sql-expert" in systemprompt.ROLE_BRIEFS
        prompt = systemprompt.worker("sql-expert", 8)
        assert "database specialist" in prompt and "Write efficient SQL." in prompt
        assert systemprompt.get("worker:sql-expert") != systemprompt.MAIN

        # unknown custom type still falls back in spawn validation logic
        assert "nonexistent-type" not in ROLES

    print("agenttypes self-test: PASS")
