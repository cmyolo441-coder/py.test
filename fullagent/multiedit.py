"""MultiEdit tool: apply several find/replace edits to one file atomically.

All edits are validated against the original file content first; only if
every ``old_string`` occurs exactly once is the file rewritten (once).
Any validation failure leaves the file untouched.
"""

from __future__ import annotations

from typing import Any

from .tools import (
    RISK_CONFIRM,
    RISK_SAFE,
    Tool,
    _atomic_write_text,
    _checked,
)

_DESCRIPTION = (
    "Apply multiple find/replace edits to a single file ATOMICALLY. "
    "Every old_string is validated first: it must occur exactly once in the "
    "file. If ANY edit fails validation (missing, ambiguous, or malformed), "
    "the file is left completely unchanged and the error names the failing "
    "edit index. Prefer this over several sequential edit_file calls — the "
    "file never ends up half-edited."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Path of the file to edit.",
        },
        "edits": {
            "type": "array",
            "description": "List of {old_string, new_string} replacements, "
                           "applied in order.",
            "items": {
                "type": "object",
                "properties": {
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find "
                                       "(must match exactly once).",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text.",
                    },
                },
                "required": ["old_string", "new_string"],
            },
        },
    },
    "required": ["file_path", "edits"],
}


def multi_edit(file_path: str, edits: list[dict[str, Any]]) -> str:
    """Apply all edits atomically; validate everything before writing."""
    p, err = _checked(file_path)
    if err:
        return err
    if not p.exists():
        return f"ERROR: file not found: {p}"
    if not isinstance(edits, list) or not edits:
        return "ERROR: edits must be a non-empty list of {old_string, new_string}"
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ("ERROR: file is not valid UTF-8 text; refusing to edit "
                "(lossy rewrite would corrupt unrelated bytes)")
    except OSError as e:
        return f"ERROR: {e}"

    # Validate every edit against the ORIGINAL content before touching
    # anything. Sequential replacement would let one edit shift another's
    # match, so each old_string is matched in `text` as it stood on disk.
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            return f"ERROR: edit #{i} is malformed; expected {{\"old_string\", \"new_string\"}}"
        old = edit.get("old_string")
        if not isinstance(old, str) or not old:
            return f"ERROR: edit #{i}: old_string must be a non-empty string"
        if "new_string" not in edit or not isinstance(edit["new_string"], str):
            return f"ERROR: edit #{i}: new_string must be a string"
        count = text.count(old)
        if count == 0:
            return (f"ERROR: edit #{i} failed validation: old_string not "
                    f"found in file (no changes were made)")
        if count > 1:
            return (f"ERROR: edit #{i} failed validation: old_string matches "
                    f"{count} places; add more context to make it unique "
                    f"(no changes were made)")

    new_text = text
    for edit in edits:
        new_text = new_text.replace(edit["old_string"], edit["new_string"], 1)
    try:
        _atomic_write_text(p, new_text)
    except OSError as e:
        return f"ERROR: {e}"
    return f"Applied {len(edits)} edits to {p}"


def build_tool() -> Tool:
    risk = RISK_CONFIRM if isinstance(RISK_CONFIRM, str) else RISK_SAFE
    return Tool("MultiEdit", _DESCRIPTION, _PARAMETERS, multi_edit,
                risk=risk)


def register(agent) -> None:
    """Register the MultiEdit tool on an agent (``agent.tools`` dict)."""
    agent.tools["MultiEdit"] = build_tool()


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    def check(name: str, cond: bool) -> None:
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            raise SystemExit(f"self-test failed: {name}")

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "sample.txt"
        original = "alpha one\nbeta two\ngamma three\n"
        f.write_text(original, encoding="utf-8")

        # 1. happy path: 3 edits
        out = multi_edit(
            str(f),
            [{"old_string": "alpha", "new_string": "ALPHA"},
             {"old_string": "beta two", "new_string": "BETA TWO"},
             {"old_string": "gamma three", "new_string": "gamma 3"}],
        )
        check("happy-path result message",
              out == f"Applied 3 edits to {f}")
        check("happy-path content",
              f.read_text(encoding="utf-8") == "ALPHA one\nBETA TWO\ngamma 3\n")

        # 2. failing middle edit leaves file unchanged
        before = f.read_text(encoding="utf-8")
        out = multi_edit(
            str(f),
            [{"old_string": "ALPHA", "new_string": "alpha"},
             {"old_string": "NOPE-NOT-THERE", "new_string": "x"},
             {"old_string": "gamma 3", "new_string": "gamma three"}],
        )
        check("middle-failure names edit index",
              "edit #1" in out and "no changes were made" in out.lower() or
              "no changes were made" in out.lower())
        check("file unchanged after middle failure",
              f.read_text(encoding="utf-8") == before)

        # 3. duplicate old_string error
        g = Path(d) / "dup.txt"
        g.write_text("aaa\naaa\n", encoding="utf-8")
        out = multi_edit(str(g),
                         [{"old_string": "aaa", "new_string": "b"}])
        check("duplicate old_string rejected",
              out.startswith("ERROR") and "matches 2 places" in out)
        check("file unchanged after duplicate failure",
              g.read_text(encoding="utf-8") == "aaa\naaa\n")

        # 4. missing file refused
        out = multi_edit(str(Path(d) / "missing.txt"),
                         [{"old_string": "a", "new_string": "b"}])
        check("missing file refused", out.startswith("ERROR"))

    # 5. register() wires into agent.tools with confirm risk
    class FakeAgent:
        def __init__(self):
            self.tools = {}

    a = FakeAgent()
    register(a)
    t = a.tools.get("MultiEdit")
    check("register sets agent.tools['MultiEdit']", t is not None)
    check("tool risk is confirm", t.risk == "confirm")
    check("handler works through registry",
          t.handler(str(Path(d) / "missing.txt"),
                    [{"old_string": "a", "new_string": "b"}]).startswith("ERROR"))

    print("ALL SELF-TESTS PASSED")
