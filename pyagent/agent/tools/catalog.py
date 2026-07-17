"""Aggregates every tool group into a single default registry list."""

from __future__ import annotations

from .archive_tools import get_archive_tools
from .base import Tool
from .builtins import get_builtin_tools
from .color_tools import get_color_tools
from .convert_tools import get_convert_tools
from .data_tools import get_data_tools
from .edit_tools import get_edit_tools
from .encoding_tools import get_encoding_tools
from .git_tools import get_git_tools
from .http_tools import get_http_tools
from .math_tools import get_math_tools
from .network_tools import get_network_tools
from .process_tools import get_process_tools
from .python_tools import get_python_tools
from .random_tools import get_random_tools
from .search_tools import get_search_tools
from .system_tools import get_system_tools
from .text_tools import get_text_tools


def get_all_tools() -> list[Tool]:
    tools: list[Tool] = []
    for group in (
        get_builtin_tools,
        get_git_tools,
        get_python_tools,
        get_http_tools,
        get_data_tools,
        get_encoding_tools,
        get_system_tools,
        get_search_tools,
        get_math_tools,
        get_edit_tools,
        get_archive_tools,
        get_random_tools,
        get_text_tools,
        get_network_tools,
        get_convert_tools,
        get_process_tools,
        get_color_tools,
    ):
        tools.extend(group())
    return tools
