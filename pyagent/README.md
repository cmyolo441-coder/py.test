ï»¿# Advanced Terminal AI Agent

The world''s most advanced, production-grade terminal AI agent, built in pure Python. Beautiful animated TUI, multi-LLM support, 65+ real tools, agentic tool-calling, guardrails, sessions, personas and a plugin system.

100+ files, fully real working code, with a test suite (90+ tests).

## Features

- Animated Rich TUI: flowing-gradient banner, typewriter intro, shimmering thinking indicator, framed prompt box, live streaming markdown, colored tool cards
- Live theming: switch between **neon, cyberpunk, pastel, matrix, solarized** at runtime with `/theme`; the whole UI (banner, prompt box, bubbles, spinners, status bar) restyles instantly
- Chat bubbles: user messages render as right-aligned bubbles; assistant answers stream as live markdown with syntax-highlighted code blocks
- Rich effects: gradient headings, animated progress bars, fade-in / slide-in reveals, celebration confetti on task completion, and a Matrix digital-rain animation (`/matrix`)
- Selectable spinners: braille, dots, moon, line, arc, star, bounce (`/spinner`)
- Live slash-command menu: floating dropdown with command descriptions while you type
- Status bar: live UTC clock, provider/model, token counter and active theme after every turn
- Keyboard shortcuts overlay (`/keys`)
- 8 providers: zen (opencode.ai), OpenAI, Anthropic, Groq, Gemini, Mistral, Together, and local Ollama
- 65+ tools: shell, files, editing, search, git, python exec, HTTP, data (json/csv), encoding, text, math, system, network, random, archive, unit-convert, process, color
- Agentic loop with streaming and multi-step tool use
- **Goal Mode** (`/goal`): persistent autonomous mode — stays active across multiple goals until you type `/chat`; auto godmode effort, full tool capacity, auto-approve
- Guardrails: dangerous-command detection + per-turn tool-call budget
- Personas: coder, sysadmin, researcher, concise, default
- Session persistence + markdown/JSON export
- Plugin system: drop tools into ~/.terminal_agent/plugins
- CLI: provider/model/theme/spinner flags and one-shot prompts

## Installation

### Option 1 — pip install (recommended)

    pip install git+https://github.com/cmyolo441-coder/py.test

After install, run with:

    agent

### Option 2 — Binary (no source needed)

Download `dist/agent` from the repo or build it yourself:

    shiv -o dist/agent -e agent.app:main .

Then run directly:

    ./dist/agent
    # or copy to PATH:
    cp dist/agent /usr/local/bin/agent && agent

Requires Python 3.10+ on the target system (all other deps are bundled).

### Option 3 — Source

    git clone https://github.com/cmyolo441-coder/py.test
    cd py.test
    pip install -r requirements.txt
    python main.py

Pick a theme and spinner at startup:

    python main.py --theme cyberpunk --spinner moon

One-shot / scripted:

    python main.py -p zen -m big-pickle -c "what is 2+2?"

The Zen provider ships with a built-in key and 3 free models: mimo-v2.5-free, big-pickle, deepseek-v4-flash-free.

## Commands

    make install    # install deps
    make test       # run tests
    make health     # verify install (no API calls)
    make tools      # list all tools
    make run        # start the agent

Slash commands include `/goal /chat /help /theme /spinner /keys /matrix /status /model /models /provider /tools /persona /config /export /auto /anim /clear /save /tokens /exit`.

## Architecture

    main.py                 -> entry point (CLI parsing)
    agent/
      app.py                -> application shell + turn loop
      core.py               -> reasoning + tool-execution loop
      guardrails.py         -> safety layer
      personas.py           -> system-prompt presets
      config.py, memory.py  -> config + conversation state
      cli.py                -> argument parsing
      ui.py, effects.py     -> animated Rich TUI
      providers/            -> 8 LLM backends behind one interface
      tools/                -> 17 tool groups (65+ tools)
      commands/             -> modular slash-command framework
      session/              -> named sessions + export
      plugins/              -> runtime plugin loader
      utils/                -> logging, tokens, text, files, security, timing, retry
    tests/                  -> 90+ unit tests
    scripts/                -> healthcheck + tool listing
    docs/                   -> architecture, tools, providers

See docs/ARCHITECTURE.md, docs/TOOLS.md and docs/PROVIDERS.md for details.

## Safety

Dangerous tools (shell, python exec, file writes) require confirmation unless /auto is on. Guardrails independently scan shell commands for destructive patterns and cap tool calls per turn.

## License

MIT (see LICENSE)
