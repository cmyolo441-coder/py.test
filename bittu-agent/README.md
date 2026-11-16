# ✦ BITTU AGENT ULTRA — v2 Next Gen Terminal AI Harness

**Developer by Bittu Chauhan**

Ultra-smooth • Beautiful • Very advanced • Max-power Go TUI harness with **double-line prompt**, **animated gradient**, **streaming**, **multi-provider**, **sessions**, **modes**, **command palette**, **file finder**, **approval** and **auto-compact**.

## Stack
- **Go 1.26 + BubbleTea + Bubbles + Lip Gloss + Glamour + go-toml**
- **OpenCode Console bridge** (`http://127.0.0.1:28894`) — **`hy4-flash-free`** (free, 1M context, multimodal) — default
  - Free models are only reachable through the OpenCode harness, so BITTU talks to the running OpenCode desktop service. The Basic-auth password is auto-read from `~/.config/opencode/service.json`.
  - Needs the OpenCode desktop app running. Free usage is generous but rate-limited (the "near-unlimited" 2-week promo).
- Also: **SambaNova / Groq / OpenAI / OpenRouter** (switch with `/provider`)
- **Max context: 1,048,576** • **Max output: 16,384 tokens**

## Quick start
```powershell
go mod tidy
go build -o bittu.exe .
.\bittu.exe
.\bittu.exe --theme matrix --provider groq --mode build
.\bittu.exe --provider opencode --once "hello"   # free hy4-flash-free
.\bittu.exe --once "hello"
```

## Config — `config.toml`
```toml
[provider.sambanova]
base_url = "https://api.sambanova.ai/v1"
api_key = "YOUR_KEY_HERE"
model = "MiniMax-M3.1-Flash"
max_context_window = 1048576
max_output_tokens = 16384

[providers]
active = "opencode"

[providers.opencode]
name = "OpenCode Console"
base_url = "http://127.0.0.1:28894"   # OpenCode desktop service (auto-detected default)
api_key_env = "OPENCODE_API_KEY"      # optional; service.json password is auto-read
model = "hy4-flash-free"
context_window = 1048576
max_tokens = 65536
timeout = 180
```

## Keys & palette
| Key | Action |
|---|---|
| `Enter` | Send |
| `Shift+Enter` | New line |
| `Ctrl+K` | Command palette |
| `Ctrl+P` | File finder |
| `Ctrl+A` | Cycle mode |
| `Ctrl+J` | Sidebar toggle |
| `Ctrl+S` | Save session |
| `Ctrl+O` | Sessions |
| `Ctrl+R` | Reasoning |
| `Ctrl+M` | Model |
| `Ctrl+T` | Theme |
| `Ctrl+D` | Cancel stream |
| `Ctrl+Shift+C` | Compact |
| `Ctrl+Q` | Quit |
| `!cmd` | Shell fast-path |
| `@file` | Attach file |

Slash: `/help /model /provider /mode /sessions /save /resume /export /compact /context /diff /review /fmt /test /run /tree /search /add /undo /status /config /developer`

## Modes
| Mode | Policy |
|---|---|
| `build` 🛠️ | full auto |
| `plan` 🗺️ | plan first, approval |
| `ask` 💬 | read-only |
| `research` 🔬 | web+grep |
| `debug` 🐛 | reproduce→fix |
| `review` 🔭 | audit only |

## Tools (AI can call via ```tool blocks)
`shell` `read_file` `write_file` `edit_file` `list_dir` `tree` `grep` `web_fetch` `git_status` `git_diff` `go_fmt` `go_test` `now`

Example:
````md
```tool
git_diff: 
```
````

## Sessions
Auto-save to `~/.bittu-agent/sessions/` — `/save /sessions /resume /export`

## Themes
`devin` (default Devin-slate premium) `neon` `matrix` `sunset` `ocean` `midnight`

Prompt box is **Devin/Claude style true double-border** (`lipgloss.DoubleBorder ╔═╗ ║ ╚═╝`) with dark `#111116` bg, thin `─` divider and `❯ mode` bottom pill bar — no rainbow clutter.

---

Built with ❤️ by **Bittu Chauhan** — ULTRA v2
