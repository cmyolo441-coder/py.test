"""Tools exposing Project Brain workspace intelligence."""

from __future__ import annotations

from .base import Tool, ToolResult


def _project_radar(root: str = ".") -> ToolResult:
    from ..project_brain import ProjectBrain
    brain = ProjectBrain(root)
    brain.scan()
    return ToolResult(output=brain.render_radar())


def _project_link_error(error_text: str, root: str = ".") -> ToolResult:
    from ..project_brain import ProjectBrain
    brain = ProjectBrain(root)
    brain.scan()
    return ToolResult(output=brain.link_error(error_text))


def _project_next_actions(goal: str = "", error_text: str = "", root: str = ".") -> ToolResult:
    from ..project_brain import ProjectBrain
    brain = ProjectBrain(root)
    brain.scan()
    return ToolResult(output=brain.suggest_next_actions(goal=goal, error_text=error_text))


def _project_timeline(root: str = ".", limit: int = 20) -> ToolResult:
    from ..project_brain import ProjectBrain
    brain = ProjectBrain(root)
    return ToolResult(output=brain.timeline(limit=limit))


def get_project_brain_tools() -> list[Tool]:
    s = {"type": "string"}
    return [
        Tool(
            "project_radar",
            "Scan the current workspace and summarize files, Python modules, tests, entrypoints, configs, and recent changes.",
            {"type": "object", "properties": {"root": {"type": "string", "default": "."}}},
            _project_radar,
        ),
        Tool(
            "project_link_error",
            "Parse a Python traceback/error and link file:line frames to project code symbols.",
            {"type": "object", "properties": {"error_text": s, "root": {"type": "string", "default": "."}}, "required": ["error_text"]},
            _project_link_error,
        ),
        Tool(
            "project_next_actions",
            "Suggest next best project actions from a goal and/or traceback using real workspace signals.",
            {"type": "object", "properties": {"goal": {"type": "string", "default": ""}, "error_text": {"type": "string", "default": ""}, "root": {"type": "string", "default": "."}}},
            _project_next_actions,
        ),
        Tool(
            "project_timeline",
            "Show the Project Brain event timeline for the workspace.",
            {"type": "object", "properties": {"root": {"type": "string", "default": "."}, "limit": {"type": "integer", "default": 20}}},
            _project_timeline,
        ),
    ]
