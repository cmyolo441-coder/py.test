"""Export a session to markdown or JSON files."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .session import Session


def export_json(session: Session, path: str | Path) -> Path:
    p = Path(path)
    p.write_text(json.dumps(session.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def export_markdown(session: Session, path: str | Path) -> Path:
    p = Path(path)
    created = datetime.datetime.fromtimestamp(session.created_at).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {session.title}",
        "",
        f"- **Session ID:** `{session.id}`",
        f"- **Provider:** {session.provider}",
        f"- **Model:** {session.model}",
        f"- **Created:** {created}",
        "",
        "---",
        "",
    ]
    for msg in session.messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b) for b in content
            )
        label = {"user": "🧑 You", "assistant": "🤖 Agent", "tool": "🛠 Tool"}.get(role, role)
        lines.append(f"### {label}")
        lines.append("")
        lines.append(str(content))
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
