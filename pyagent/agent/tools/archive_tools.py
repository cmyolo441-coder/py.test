"""Archive tools: zip/unzip and list archive contents."""

from __future__ import annotations

import zipfile
from pathlib import Path

from .base import Tool, ToolResult

_SKIP = {".git", "__pycache__", ".venv", "venv", "node_modules"}


def zip_create(output: str, paths: list[str] | None = None, root: str = ".") -> ToolResult:
    out = Path(output)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        if paths:
            for pth in paths:
                p = Path(pth)
                if p.exists():
                    zf.write(p, p.name)
                    count += 1
        else:
            base = Path(root)
            for f in base.rglob("*"):
                if any(part in _SKIP for part in f.parts):
                    continue
                if f.is_file():
                    zf.write(f, f.relative_to(base))
                    count += 1
    return ToolResult(output=f"Created {output} with {count} files")


def zip_list(path: str) -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    try:
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return ToolResult(output="Not a valid zip file", success=False)
    return ToolResult(output="\n".join(names), metadata={"count": len(names)})


def zip_extract(path: str, dest: str = ".") -> ToolResult:
    p = Path(path)
    if not p.exists():
        return ToolResult(output=f"Not found: {path}", success=False)
    try:
        with zipfile.ZipFile(p) as zf:
            zf.extractall(dest)
            n = len(zf.namelist())
    except zipfile.BadZipFile:
        return ToolResult(output="Not a valid zip file", success=False)
    return ToolResult(output=f"Extracted {n} files to {dest}")


def get_archive_tools() -> list[Tool]:
    return [
        Tool("zip_create", "Create a zip archive from paths or a directory tree.",
             {"type": "object", "properties": {
                 "output": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}},
                 "root": {"type": "string", "default": "."}}, "required": ["output"]},
             zip_create, dangerous=True),
        Tool("zip_list", "List the contents of a zip archive.",
             {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, zip_list),
        Tool("zip_extract", "Extract a zip archive to a destination directory.",
             {"type": "object", "properties": {
                 "path": {"type": "string"}, "dest": {"type": "string", "default": "."}},
              "required": ["path"]}, zip_extract, dangerous=True),
    ]
