"""Data tools: JSON/CSV parsing, querying and conversion."""

from __future__ import annotations

import csv
import io
import json

from .base import Tool, ToolResult

_MAX = 10000


def json_query(data: str, path: str = "") -> ToolResult:
    """Parse JSON and optionally follow a dotted path (e.g. 'a.b.0.c')."""
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as exc:
        return ToolResult(output=f"Invalid JSON: {exc}", success=False)
    if path:
        for part in path.split("."):
            try:
                if isinstance(obj, list):
                    obj = obj[int(part)]
                else:
                    obj = obj[part]
            except (KeyError, IndexError, ValueError) as exc:
                return ToolResult(output=f"Path error at '{part}': {exc}", success=False)
    return ToolResult(output=json.dumps(obj, indent=2)[:_MAX])


def json_format(data: str, indent: int = 2) -> ToolResult:
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as exc:
        return ToolResult(output=f"Invalid JSON: {exc}", success=False)
    return ToolResult(output=json.dumps(obj, indent=indent, ensure_ascii=False)[:_MAX])


def csv_to_json(data: str, delimiter: str = ",") -> ToolResult:
    try:
        reader = csv.DictReader(io.StringIO(data), delimiter=delimiter)
        rows = list(reader)
    except csv.Error as exc:
        return ToolResult(output=f"CSV error: {exc}", success=False)
    return ToolResult(output=json.dumps(rows, indent=2)[:_MAX], metadata={"rows": len(rows)})


def csv_summary(data: str, delimiter: str = ",") -> ToolResult:
    reader = csv.reader(io.StringIO(data), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return ToolResult(output="(empty CSV)")
    header = rows[0]
    summary = [f"columns ({len(header)}): {', '.join(header)}", f"data rows: {len(rows) - 1}"]
    return ToolResult(output="\n".join(summary))


def get_data_tools() -> list[Tool]:
    return [
        Tool(
            name="json_query",
            description="Parse JSON and follow an optional dotted path (e.g. 'items.0.name').",
            parameters={
                "type": "object",
                "properties": {"data": {"type": "string"}, "path": {"type": "string"}},
                "required": ["data"],
            },
            func=json_query,
        ),
        Tool(
            name="json_format",
            description="Pretty-print / validate a JSON string.",
            parameters={
                "type": "object",
                "properties": {"data": {"type": "string"}, "indent": {"type": "integer", "default": 2}},
                "required": ["data"],
            },
            func=json_format,
        ),
        Tool(
            name="csv_to_json",
            description="Convert CSV text into a JSON array of objects.",
            parameters={
                "type": "object",
                "properties": {"data": {"type": "string"}, "delimiter": {"type": "string", "default": ","}},
                "required": ["data"],
            },
            func=csv_to_json,
        ),
        Tool(
            name="csv_summary",
            description="Summarise a CSV: column names and row count.",
            parameters={
                "type": "object",
                "properties": {"data": {"type": "string"}, "delimiter": {"type": "string", "default": ","}},
                "required": ["data"],
            },
            func=csv_summary,
        ),
    ]
