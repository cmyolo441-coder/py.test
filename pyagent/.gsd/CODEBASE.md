# Codebase Map

Generated: 2026-07-11T18:10:28Z | Files: 245 | Described: 0/245
<!-- gsd:codebase-meta {"generatedAt":"2026-07-11T18:10:28Z","fingerprint":"1dd7b6fd9784b67c29fd286bd148c77a397cdd40","fileCount":245,"truncated":false} -->

### (root)/
- `.editorconfig`
- `.env.example`
- `.gitignore`
- `.gitlab-ci.yml`
- `agent.spec`
- `bhcjkbsdjk.zip`
- `CHANGELOG.md`
- `config.example.yaml`
- `CONTRIBUTING.md`
- `Dockerfile`
- `LICENSE`
- `main.py`
- `Makefile`
- `pyproject.toml`
- `pytest.ini`
- `README.md`
- `requirements.txt`
- `session-1783243289.md`

### agent/
- *(116 files: 116 .py)*

### agent/commands/
- `agent/commands/__init__.py`
- `agent/commands/base.py`
- `agent/commands/builtin_commands.py`
- `agent/commands/config_command.py`
- `agent/commands/enterprise_commands.py`
- `agent/commands/feature_commands.py`
- `agent/commands/goal_command.py`
- `agent/commands/persona_command.py`
- `agent/commands/registry.py`
- `agent/commands/session_commands.py`
- `agent/commands/ui_commands.py`
- `agent/commands/v3_commands.py`
- `agent/commands/v4_commands.py`

### agent/plugins/
- `agent/plugins/__init__.py`
- `agent/plugins/example_calculator.py`
- `agent/plugins/example_plugin.py`
- `agent/plugins/loader.py`

### agent/providers/
- `agent/providers/__init__.py`
- `agent/providers/anthropic_provider.py`
- `agent/providers/base.py`
- `agent/providers/factory.py`
- `agent/providers/gemini_provider.py`
- `agent/providers/lovable_provider.py`
- `agent/providers/mistral_provider.py`
- `agent/providers/ollama_provider.py`
- `agent/providers/openai_provider.py`
- `agent/providers/registry.py`
- `agent/providers/together_provider.py`

### agent/session/
- `agent/session/__init__.py`
- `agent/session/exporter.py`
- `agent/session/session.py`
- `agent/session/store.py`

### agent/tools/
- *(27 files: 27 .py)*

### agent/utils/
- `agent/utils/__init__.py`
- `agent/utils/env.py`
- `agent/utils/files.py`
- `agent/utils/logging.py`
- `agent/utils/retry.py`
- `agent/utils/security.py`
- `agent/utils/text.py`
- `agent/utils/timing.py`
- `agent/utils/tokens.py`

### docs/
- `docs/ARCHITECTURE.md`
- `docs/PROVIDERS.md`
- `docs/TOOLS.md`

### scripts/
- `scripts/healthcheck.py`
- `scripts/list_tools.py`

### tests/
- *(38 files: 38 .py)*
