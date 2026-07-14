# Advanced Terminal AI Agent

A production-grade terminal AI coding agent with a Codex-style TUI, multi-provider LLM support, real tool calling, autonomous startup intelligence, local codebase indexing, sessions, personas, plugins, and a large test suite.

The latest build auto-activates the full intelligence stack on `python3 main.py` / `agent` startup and warms heavy code intelligence in the background for fast prompt readiness.

## Latest highlights

- **Fast Codex-style TUI**: clean dark UI, fixed double-line prompt box, clean slash completions, no duplicate/noisy popup rows.
- **Fast startup**: 539 features activate immediately; deep repo analysis warms in the background instead of blocking the prompt.
- **456+ local intelligence features plus Quantum layer**: Enterprise, Hyper, Apex, Omega, Nova, Zenith, and Quantum startup suites.
- **123 tools / 153 commands / 11 providers** in the current full registry.
- **Standalone binary**: `dist/agent` is rebuilt with `shiv`, uses pure-Python runtime deps, and can be installed via `curl`.
- **Pip install supported** from this GitHub repository with the `pyagent` subdirectory.
- **HTTP OpenAI-compatible fallback**: OpenAI-compatible providers run without the official `openai` package, avoiding native `pydantic_core` binary issues.

## Install

### Option 1 — curl binary install

Installs the latest checked-in `dist/agent` binary to `~/.local/bin/agent`:

```bash
curl -fsSL https://raw.githubusercontent.com/cmyolo441-coder/py.test/main/pyagent/scripts/install.sh | sh
```

Then run:

```bash
agent
agent --version
```

If `agent` is not found, add this to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

> Note: the checked-in binary is a Python shiv executable with pure-Python runtime dependencies only. The previous native `pydantic_core`/OpenAI SDK bundle was removed so the binary works across Python 3.10+ environments without the `pydantic_core._pydantic_core` crash. If your shell has an older shiv cache, re-run the installer; it clears `~/.shiv/agent_*`.

### Option 2 — curl + pip source install

```bash
curl -fsSL https://raw.githubusercontent.com/cmyolo441-coder/py.test/main/pyagent/scripts/install.sh | TERMINAL_AGENT_INSTALL_MODE=pip sh
```

### Option 3 — direct pip install from GitHub

Because the Python project lives inside the `pyagent/` subdirectory, use `#subdirectory=pyagent`:

```bash
python3 -m pip install --upgrade "git+https://github.com/cmyolo441-coder/py.test.git#subdirectory=pyagent"
```

Then run:

```bash
agent
newagent
```

### Option 4 — source checkout

```bash
git clone https://github.com/cmyolo441-coder/py.test.git
cd py.test/pyagent
python3 -m pip install -r requirements.txt
python3 main.py
```

One-shot / scripted:

```bash
python3 main.py -p zen -m big-pickle -c "what is 2+2?"
```

Pick theme/spinner:

```bash
python3 main.py --theme cyberpunk --spinner moon
```

## Binary build / rebuild

Old `dist/` and `build/` artifacts are removed before every binary build.

```bash
cd py.test/pyagent
./scripts/build_binary.sh
```

Output:

```text
dist/agent
```

Run directly:

```bash
./dist/agent --version
./dist/agent
```

Install manually:

```bash
install -Dm755 dist/agent ~/.local/bin/agent
agent
```

## Runtime commands

Common Make/script commands:

```bash
make install    # install deps
make test       # run tests
make health     # verify install without API calls
make tools      # list tools
make run        # start the agent
```

Slash commands include:

```text
/help /exit /model /models /models-all /provider /tools /theme /spinner /keys
/goal /chat /status /tokens /cost /dashboard /features129 /hyper70 /apex40
/omega49 /nova71 /zenith97 /quantum83 /rag /kg /sast /sbom /memory /mcp
```

## Auto-start intelligence stack

On normal interactive startup, the agent now uses **fast start**:

1. 539 features are available immediately.
2. The prompt appears quickly.
3. Heavy local analysis warms in a background thread.
4. Once ready, later prompts automatically receive compact local context.

Startup layers include:

- **Enterprise Suite** — 129 feature profile and runtime activation.
- **Hyper Suite** — repository inventory, symbol density, docs/test/runtime hints.
- **Apex Suite** — call graph, impact scoring, workflow and verification planning.
- **Omega Suite** — semantic index, refactor engine, test matrix, runtime map, context packer.
- **Nova Suite** — symbol intelligence, similarity, change forecast, workflow DAG, docs/quality insights.
- **Zenith Suite** — LSP index, dependency graph, metrics fusion, release/cache/context/handoff engines.
- **Quantum Suite** — intent tracing, reliability risk, benchmark, UX, dataflow, contracts, scaffold, ops runbooks.

The background warmup is safe/local/offline. Dangerous tool execution still requires confirmation unless `/auto` is explicitly enabled.

## Architecture

```text
main.py                 -> source entry point
agent/app.py            -> app shell, CLI entry point, turn loop, autostart
agent/core.py           -> LLM reasoning + tool execution loop
agent/ui.py             -> Codex-style Rich/prompt_toolkit TUI
agent/autostart.py      -> zero-command startup warmup orchestrator
agent/enterprise_suite.py
agent/hyper_suite.py
agent/apex_suite.py
agent/omega_suite.py
agent/nova_suite.py
agent/zenith_suite.py
agent/quantum_suite.py  -> layered local intelligence suites
agent/providers/        -> OpenAI-compatible, Anthropic, Gemini, Ollama, etc.
agent/tools/            -> built-in tool registry
agent/commands/         -> slash command registry
agent/session/          -> session storage/export
agent/plugins/          -> runtime plugin loader
scripts/build_binary.sh -> clean + rebuild dist/agent
scripts/install.sh      -> curl installer
```

Docs:

```text
docs/ARCHITECTURE.md
docs/TOOLS.md
docs/PROVIDERS.md
```

## Safety

Dangerous tools such as shell execution, Python execution, and file writes require confirmation unless `/auto` is enabled. Guardrails scan dangerous shell patterns and cap tool calls per turn.

## Development validation

```bash
python3 -m pytest -q
python3 -m compileall -q agent scripts tests
python3 scripts/healthcheck.py
```

Current validation status from this build:

```text
427 tests passed
healthcheck: All good
binary smoke: terminal-agent 1.0.0
```

## License

MIT — see `LICENSE`.
