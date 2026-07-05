"""Filesystem helpers: safe paths, size formatting, atomic writes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def human_size(num_bytes: float) -> str:
    size = float(num_bytes)
    for unit in _UNITS:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024.0
    return f"{size:.1f} EB"


def atomic_write(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Write to a temp file then rename, avoiding partial writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def is_within(base: str | Path, target: str | Path) -> bool:
    """Return True if ``target`` is inside ``base`` (prevents path traversal)."""
    base = Path(base).resolve()
    try:
        Path(target).resolve().relative_to(base)
        return True
    except ValueError:
        return False


def safe_read(path: str | Path, max_bytes: int = 1_000_000) -> str:
    p = Path(path)
    data = p.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p
