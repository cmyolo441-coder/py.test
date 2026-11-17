"""Locate and read the user's system prompt (cached by path/mtime/size)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

_CACHE: dict = {}


def _home() -> Optional[Path]:
    env = os.environ.get("FULLAGENT_HOME")
    if env:
        return Path(env)
    try:
        return Path.home() / ".fullagent"
    except (OSError, RuntimeError):
        return None


def _candidates() -> List[Path]:
    out: List[Path] = []
    env = os.environ.get("FULLAGENT_SYSTEM_PROMPT_FILE")
    if env:
        out.append(Path(env))
    home = _home()
    if home is not None:
        out.append(home / "system_prompt.txt")
    out.append(Path("system_prompt.txt"))
    out.append(Path(__file__).resolve().parent.parent / "project.txt")
    return out


def _read(path: Path) -> Optional[str]:
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
        raw = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return None
    text = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(_CACHE) > 16:
        _CACHE.clear()
    _CACHE[key] = text
    return text


def find_user_prompt() -> Tuple[str, Optional[str]]:
    """Return (text, path) of the first non-empty prompt file, else ('', None)."""
    for p in _candidates():
        text = _read(p)
        if text:
            return text, str(p)
    return "", None


def _self_test() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.txt"
        f.write_bytes(b"\xef\xbb\xbfAlways be brief.\r\nNever lie.\r\n")
        old = os.environ.get("FULLAGENT_SYSTEM_PROMPT_FILE")
        os.environ["FULLAGENT_SYSTEM_PROMPT_FILE"] = str(f)
        try:
            text, path = find_user_prompt()
            assert text == "Always be brief.\nNever lie.", text
            assert path == str(f)
            assert find_user_prompt() == (text, path)
        finally:
            if old is None:
                os.environ.pop("FULLAGENT_SYSTEM_PROMPT_FILE", None)
            else:
                os.environ["FULLAGENT_SYSTEM_PROMPT_FILE"] = old
    print("loader ok")


if __name__ == "__main__":
    _self_test()
