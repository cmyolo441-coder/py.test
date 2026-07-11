# Contributing

Thanks for your interest in improving the terminal AI agent!

## Development setup

```bash
pip install -r requirements.txt
pip install -e ".[dev]"
```

## Running tests

```bash
python -m pytest
```

## Linting

```bash
ruff check agent tests
```

## Project conventions

- One tool group per file in `agent/tools/`, exposed via a `get_*_tools()`
  factory and wired into `agent/tools/catalog.py`.
- Providers implement `LLMProvider.chat()`; OpenAI-compatible ones subclass
  `OpenAIProvider`.
- Commands subclass `Command` and are registered in
  `agent/commands/registry.py`.
- Every new tool/command should ship with a test in `tests/`.
- Keep functions small and typed; prefer standard-library solutions.

## Adding a tool (example)

```python
# agent/tools/hello_tools.py
from .base import Tool, ToolResult

def hello(name: str) -> ToolResult:
    return ToolResult(output=f"Hello, {name}!")

def get_hello_tools() -> list[Tool]:
    return [Tool("hello", "Greet someone.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        hello)]
```

Then add `get_hello_tools` to `catalog.py`.
