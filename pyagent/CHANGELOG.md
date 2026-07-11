# Changelog

All notable changes to this project are documented here.

## [1.0.0]

### Added
- Interactive terminal AI agent with an animated Rich TUI.
- Multi-provider support: zen (opencode.ai), OpenAI, Anthropic, Groq, Gemini,
  Mistral, Together and local Ollama.
- 65+ built-in tools across shell, files, editing, search, git, Python
  execution, HTTP, data, encoding, text, math, system, network, random,
  archive, unit conversion, process and color domains.
- Agentic tool-calling loop with streaming responses.
- Modular slash-command framework (`/help`, `/model`, `/provider`, `/tools`,
  `/persona`, `/config`, `/export`, `/auto`, `/anim`, and more).
- Session persistence and markdown/JSON export.
- Guardrails: dangerous-command detection and per-turn tool-call budget.
- Persona presets (coder, sysadmin, researcher, concise, default).
- Plugin system loading user tools from `~/.terminal_agent/plugins`.
- CLI flags for provider/model selection and one-shot prompts.
- Full test suite and a `scripts/healthcheck.py` diagnostic.
