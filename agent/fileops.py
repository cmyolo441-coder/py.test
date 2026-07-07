"""Advanced file operations: read, write, edit, append, diff, copy, move, delete, search.

These give the agent full, real file-manipulation power while remaining safe and
predictable. All operations return plain-text results suitable for the model.
"""
from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path
from typing import Any, Callable


def _resolve(path: str) -> Path:
    return Path(path).expanduser()


def read_file(path: str, max_bytes: int = 128_000) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"
    data = p.read_text(encoding="utf-8", errors="replace")
    return data[:max_bytes] + ("\n... [truncated]" if len(data) > max_bytes else "")


def read_lines(path: str, start: int = 1, end: int | None = None) -> str:
    p = _resolve(path)
    if not p.is_file():
        return f"Error: not a file: {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    end = end or len(lines)
    selected = lines[max(0, start - 1) : end]
    return "\n".join(f"{start + i}: {ln}" for i, ln in enumerate(selected))


def write_file(path: str, content: str) -> str:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} bytes to {path}"


def append_file(path: str, content: str) -> str:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(content)
    return f"Appended {len(content)} bytes to {path}"


def edit_file(path: str, old: str, new: str, count: int = 0) -> str:
    """Replace ``old`` with ``new`` in a file. count=0 replaces all occurrences."""
    p = _resolve(path)
    if not p.is_file():
        return f"Error: not a file: {path}"
    text = p.read_text(encoding="utf-8")
    if old not in text:
        return "Error: target text not found; no changes made."
    occurrences = text.count(old)
    text = text.replace(old, new) if count == 0 else text.replace(old, new, count)
    p.write_text(text, encoding="utf-8")
    return f"Replaced {occurrences if count == 0 else min(count, occurrences)} occurrence(s) in {path}"


def regex_replace(path: str, pattern: str, replacement: str) -> str:
    p = _resolve(path)
    if not p.is_file():
        return f"Error: not a file: {path}"
    text = p.read_text(encoding="utf-8")
    try:
        new_text, n = re.subn(pattern, replacement, text)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"
    p.write_text(new_text, encoding="utf-8")
    return f"Made {n} replacement(s) in {path}"


def insert_at_line(path: str, line_number: int, content: str) -> str:
    p = _resolve(path)
    if not p.is_file():
        return f"Error: not a file: {path}"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = max(0, min(line_number - 1, len(lines)))
    if content and not content.endswith("\n"):
        content += "\n"
    lines.insert(idx, content)
    p.write_text("".join(lines), encoding="utf-8")
    return f"Inserted content at line {line_number} in {path}"


def delete_lines(path: str, start: int, end: int) -> str:
    p = _resolve(path)
    if not p.is_file():
        return f"Error: not a file: {path}"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    del lines[max(0, start - 1) : end]
    p.write_text("".join(lines), encoding="utf-8")
    return f"Deleted lines {start}-{end} in {path}"


def diff_files(path_a: str, path_b: str) -> str:
    a = _resolve(path_a)
    b = _resolve(path_b)
    if not a.is_file() or not b.is_file():
        return "Error: both paths must be files."
    a_lines = a.read_text(encoding="utf-8").splitlines()
    b_lines = b.read_text(encoding="utf-8").splitlines()
    diff = difflib.unified_diff(a_lines, b_lines, str(a), str(b), lineterm="")
    return "\n".join(diff) or "(files are identical)"


def copy_file(src: str, dst: str) -> str:
    s, d = _resolve(src), _resolve(dst)
    if not s.exists():
        return f"Error: source not found: {src}"
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(s, d)
    return f"Copied {src} -> {dst}"


def move_file(src: str, dst: str) -> str:
    s, d = _resolve(src), _resolve(dst)
    if not s.exists():
        return f"Error: source not found: {src}"
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return f"Moved {src} -> {dst}"


def delete_file(path: str) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"Error: not found: {path}"
    if p.is_dir():
        shutil.rmtree(p)
        return f"Deleted directory {path}"
    p.unlink()
    return f"Deleted file {path}"


def make_dir(path: str) -> str:
    _resolve(path).mkdir(parents=True, exist_ok=True)
    return f"Created directory {path}"


def search_in_files(directory: str, pattern: str, glob: str = "*") -> str:
    base = _resolve(directory)
    if not base.exists():
        return f"Error: directory not found: {directory}"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex: {exc}"
    matches: list[str] = []
    for file in base.rglob(glob):
        if not file.is_file():
            continue
        try:
            for i, line in enumerate(
                file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if regex.search(line):
                    matches.append(f"{file}:{i}: {line.strip()}")
                    if len(matches) >= 200:
                        matches.append("... [truncated]")
                        return "\n".join(matches)
        except OSError:
            continue
    return "\n".join(matches) or "No matches found."


def file_info(path: str) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"Error: not found: {path}"
    st = p.stat()
    return (
        f"path={p}\ntype={'dir' if p.is_dir() else 'file'}\n"
        f"size={st.st_size} bytes\nmodified={st.st_mtime}"
    )


# Schema registrations for tool/function calling.
def _schema(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def register() -> list[tuple[dict[str, Any], Callable[..., str]]]:
    s = {"type": "string"}
    i = {"type": "integer"}
    return [
        (_schema("fs_read_file", "Read a text file.", {"path": s}, ["path"]), read_file),
        (_schema("fs_read_lines", "Read numbered lines from a file.", {"path": s, "start": i, "end": i}, ["path"]), read_lines),
        (_schema("fs_write_file", "Create/overwrite a file.", {"path": s, "content": s}, ["path", "content"]), write_file),
        (_schema("fs_append_file", "Append text to a file.", {"path": s, "content": s}, ["path", "content"]), append_file),
        (_schema("fs_edit_file", "Replace text in a file.", {"path": s, "old": s, "new": s, "count": i}, ["path", "old", "new"]), edit_file),
        (_schema("fs_regex_replace", "Regex replace in a file.", {"path": s, "pattern": s, "replacement": s}, ["path", "pattern", "replacement"]), regex_replace),
        (_schema("fs_insert_at_line", "Insert content at a line.", {"path": s, "line_number": i, "content": s}, ["path", "line_number", "content"]), insert_at_line),
        (_schema("fs_delete_lines", "Delete a line range.", {"path": s, "start": i, "end": i}, ["path", "start", "end"]), delete_lines),
        (_schema("fs_diff", "Unified diff of two files.", {"path_a": s, "path_b": s}, ["path_a", "path_b"]), diff_files),
        (_schema("fs_copy", "Copy a file.", {"src": s, "dst": s}, ["src", "dst"]), copy_file),
        (_schema("fs_move", "Move/rename a file.", {"src": s, "dst": s}, ["src", "dst"]), move_file),
        (_schema("fs_delete", "Delete a file or directory.", {"path": s}, ["path"]), delete_file),
        (_schema("fs_mkdir", "Create a directory.", {"path": s}, ["path"]), make_dir),
        (_schema("fs_search", "Search text across files by regex.", {"directory": s, "pattern": s, "glob": s}, ["directory", "pattern"]), search_in_files),
        (_schema("fs_info", "Get file metadata.", {"path": s}, ["path"]), file_info),
    ]
