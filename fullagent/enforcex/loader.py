"""The user's system prompt lives ONLY in fullagent/systemprompt.py
(the SYSTEM_PROMPT slot). This module just reads it from there."""
from __future__ import annotations

from typing import Optional, Tuple


def find_user_prompt() -> Tuple[str, Optional[str]]:
    """Return (text, path-of-systemprompt.py), or ('', None) if the slot is empty."""
    from .. import systemprompt as sp
    return (sp.USER_PROMPT, sp.USER_PROMPT_PATH) if sp.USER_PROMPT else ("", None)


def _self_test() -> None:
    text, path = find_user_prompt()
    assert isinstance(text, str) and (path is None or path.endswith("systemprompt.py"))
    print("loader ok")


if __name__ == "__main__":
    _self_test()
