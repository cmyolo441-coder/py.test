# Terminal AI Agent

A clean, minimal terminal AI coding assistant with multi-provider LLM support, real tool calling, and a Rich-powered TUI.

## Features

- **7 LLM providers**: OpenAI, Anthropic, Groq, Gemini, Mistral, Together, Ollama (local)
- **40+ tools**: shell, file I/O, git, search, edit, Python exec, math, data, encoding, etc.
- **Clean TUI**: Rich-powered with live streaming, slash-command completion, themes
- **Tool calling**: agent reads files, runs commands, and writes code through real tools
- **Cooperative cancellation**: press Esc to stop generation mid-stream
- **Plugin system**: drop `.py` files in `~/.terminal_agent/plugins/` to add tools

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py                          # interactive mode
python main.py -p openai -m gpt-4o     # pick provider/model
python main.py -c "what is 2+2?"       # one-shot prompt
python main.py --no-anim               # disable animations
python main.py -t neon                  # pick theme
```

## Providers

Set the API key for your provider:

```bash
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
export GROQ_API_KEY=gsk_...
export GEMINI_API_KEY=...
export MISTRAL_API_KEY=...
export TOGETHER_API_KEY=...
# Ollama: no key needed (local)
```

## Commands

Type `/help` in the agent to see all commands. Key ones:

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/provider <name>` | Switch provider |
| `/model <name>` | Switch model |
| `/models` | List available models |
| `/tools` | List available tools |
| `/theme <name>` | Switch theme (default/neon/pastel/matrix) |
| `/persona <name>` | Switch persona (coder/sysadmin/researcher/concise) |
| `/auto` | Toggle auto-approve for dangerous tools |
| `/clear` | Clear conversation |
| `/exit` | Exit |

## Tests

```bash
python -m pytest -q
```

## License

MIT
