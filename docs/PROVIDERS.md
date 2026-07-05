# Providers guide

The agent supports many LLM backends behind one interface.

| Provider   | Env var             | Default model                              | Key? |
|------------|---------------------|--------------------------------------------|------|
| zen        | `ZEN_API_KEY`       | `mimo-v2.5-free`                           | yes  |
| openai     | `OPENAI_API_KEY`    | `gpt-4o`                                   | yes  |
| anthropic  | `ANTHROPIC_API_KEY` | `claude-3-5-sonnet-20241022`              | yes  |
| groq       | `GROQ_API_KEY`      | `llama-3.3-70b-versatile`                 | yes  |
| gemini     | `GEMINI_API_KEY`    | `gemini-1.5-flash`                        | yes  |
| mistral    | `MISTRAL_API_KEY`   | `mistral-large-latest`                    | yes  |
| together   | `TOGETHER_API_KEY`  | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | yes  |
| ollama     | *(none)*            | `llama3.1`                                | no   |

## Zen models

The Zen provider (opencode.ai) exposes several free models:

- `mimo-v2.5-free`
- `big-pickle`
- `deepseek-v4-flash-free`

Switch at runtime with `/provider zen` then `/model big-pickle`, or list them
with `/models`.

## Switching

- Interactive: `/provider <name>` and `/model <name>`
- CLI: `python main.py -p gemini -m gemini-1.5-flash`
- Environment: `AGENT_PROVIDER`, `AGENT_MODEL`
