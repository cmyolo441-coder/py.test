# Architecture

This document describes how the terminal AI agent is structured.

## High-level flow

```
User input
   │
   ▼
App (agent/app.py) ── slash command? ──► CommandRegistry ──► Command.run()
   │ no
   ▼
Agent.send() (agent/core.py)
   │
   ▼
LLMProvider.chat()  ◄── tool schemas from ToolRegistry
   │
   ├─ text response ─────────────► UI streams markdown
   │
   └─ tool_calls ─► ToolRegistry.execute() ─► results fed back to the model
                          (loop until final answer or max iterations)
```

## Packages

- **agent/providers** — one class per LLM backend, all implementing
  `LLMProvider.chat()`. OpenAI-compatible providers (zen, groq, gemini,
  mistral, together, ollama) reuse `OpenAIProvider`.
- **agent/tools** — each tool group lives in its own module and exposes a
  `get_*_tools()` factory. `catalog.py` aggregates them; `registry.py` stores
  them and produces provider-specific JSON schemas.
- **agent/commands** — modular slash-command framework. Each command is a class
  with `name`, `aliases` and `run()`.
- **agent/session** — named conversations with persistence and export.
- **agent/utils** — logging, token estimation, text/file helpers, security,
  timing and retry.
- **agent/plugins** — loads user tools from `~/.terminal_agent/plugins/*.py`.
- **agent/ui.py + agent/effects.py** — the Rich TUI and its animations.

## The agent loop

`Agent.send()` appends the user message, then repeatedly calls the provider.
When the model returns tool calls, each tool is executed (with optional
approval for dangerous tools) and the results are appended in the correct
provider-specific shape. The loop ends when the model returns a plain answer or
`max_tool_iterations` is reached.

## Extending

- **Add a tool:** create `agent/tools/<name>_tools.py` with a `get_<name>_tools()`
  factory and wire it into `catalog.py`.
- **Add a provider:** create `agent/providers/<name>_provider.py` and register it
  in `factory.py` and `registry.py`.
- **Add a command:** create a `Command` subclass and register it in
  `commands/registry.py`.
