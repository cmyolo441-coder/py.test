"""Assemble the final system prompt without altering the user's text."""
from __future__ import annotations

from typing import Dict, Optional

from .rules import checklist


def compose(user_prompt: str, extras: Optional[Dict[str, str]] = None) -> str:
    """User prompt verbatim first, then extras, then the recency checklist."""
    out = user_prompt or ""
    blocks = []
    for name, body in (extras or {}).items():
        if body and body.strip():
            blocks.append(f"[{name}]\n{body.strip()}")
    cl = checklist(out)
    if cl:
        blocks.append(cl)
    if blocks:
        sep = "" if out.endswith("\n\n") else ("\n" if out.endswith("\n") else "\n\n")
        out = out + (sep if out else "") + "\n\n".join(blocks)
    return out


def _self_test() -> None:
    up = "Always answer briefly.\nNever swear.\n"
    r = compose(up, {"env": "linux"})
    assert r.startswith(up)
    assert "[env]\nlinux" in r and r.rstrip().endswith("- Never swear.")
    assert compose("") == "" and compose("hello") == "hello"
    print("compose ok")


if __name__ == "__main__":
    _self_test()
