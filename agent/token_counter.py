"""Real, accurate token counter with per-provider encoders and cost tracking.

This is the enterprise-grade token accounting layer. It:
  * Uses tiktoken for OpenAI/zen/groq/mistral/together (cl100k_base / o200k_base).
  * Uses a word-ratio heuristic for Anthropic (no public tokenizer) calibrated
    against Claude's known ~3.5 chars/token average.
  * Uses character counting for local Ollama models (llama tokenizers vary).
  * Tracks per-turn, per-session and per-goal usage with cost in USD/INR.
  * Persists a rolling usage log so costs survive restarts.
  * Exposes a live snapshot the UI can render in the status bar.

Thread-safe, dependency-free fallback when tiktoken is unavailable.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

try:
    import tiktoken
    _HAS_TIKTOKEN = True
except Exception:  # pragma: no cover
    _HAS_TIKTOKEN = False

# Per-1K-token USD pricing (input, output). Update as providers change.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4-turbo": (0.01, 0.03),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "claude-3-5-sonnet-20241022": (0.003, 0.015),
    "claude-3-5-sonnet-latest": (0.003, 0.015),
    "claude-3-opus-latest": (0.015, 0.075),
    "claude-3-haiku-20240307": (0.00025, 0.00125),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "gemini-1.5-pro": (0.00125, 0.005),
    "gemini-2.0-flash": (0.0001, 0.0004),
    "llama-3.3-70b-versatile": (0.00059, 0.00079),
    "mistral-large-latest": (0.002, 0.006),
    "mistral-small-latest": (0.0002, 0.0006),
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": (0.00088, 0.00088),
    "mimo-v2.5-free": (0.0, 0.0),
    "big-pickle": (0.0, 0.0),
    "deepseek-v4-flash-free": (0.0, 0.0),
    "zyloo/glm-5.1": (0.0005, 0.0015),
    "llama3.1": (0.0, 0.0),  # local ollama
    "llama3.2": (0.0, 0.0),
    "qwen2.5": (0.0, 0.0),
}

# Per-provider token-estimation strategy.
_PROVIDER_STRATEGY: dict[str, str] = {
    "openai": "tiktoken",
    "zen": "tiktoken",
    "zyloo": "tiktoken",
    "groq": "tiktoken",
    "mistral": "tiktoken",
    "together": "tiktoken",
    "anthropic": "claude_heuristic",
    "gemini": "char_heuristic",
    "ollama": "char_heuristic",
}

# Cache encoders so we don't re-instantiate per call.
_ENCODERS: dict[str, Any] = {}
_ENCODER_LOCK = threading.Lock()

USD_TO_INR = 83.0  # approximate; updated periodically


def _get_encoder(model: str) -> Any | None:
    if not _HAS_TIKTOKEN:
        return None
    with _ENCODER_LOCK:
        if model in _ENCODERS:
            return _ENCODERS[model]
        enc = None
        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            try:
                # o200k_base is used by gpt-4o family; cl100k_base by gpt-4/3.5.
                if "gpt-4o" in model or "o1" in model or "o3" in model:
                    enc = tiktoken.get_encoding("o200k_base")
                else:
                    enc = tiktoken.get_encoding("cl100k_base")
            except Exception:
                enc = None
        _ENCODERS[model] = enc
        return enc


def count_tokens(text: str, model: str = "gpt-4o", provider: str = "openai") -> int:
    """Return an accurate token count for ``text`` under ``model``.

    Falls back gracefully when tiktoken is missing or the model is unknown.
    Never raises.
    """
    if not text:
        return 0
    strategy = _PROVIDER_STRATEGY.get(provider, "char_heuristic")
    if strategy == "tiktoken":
        enc = _get_encoder(model)
        if enc is not None:
            try:
                return len(enc.encode(text))
            except Exception:
                pass
    if strategy == "claude_heuristic":
        # Claude averages ~3.5 chars/token; use a blend of chars and words.
        words = len(text.split())
        return max(int(len(text) / 3.5), int(words * 1.35))
    # char_heuristic — used for Gemini and local Ollama models.
    return max(1, len(text) // 4)


def count_message_tokens(messages: list[dict[str, Any]], model: str, provider: str) -> int:
    """Sum tokens across a message list, including per-message overhead."""
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            # Anthropic-style content blocks.
            content = " ".join(
                b.get("text", str(b)) if isinstance(b, dict) else str(b) for b in content
            )
        total += count_tokens(str(content), model, provider) + 4  # role/formatting overhead
    return total


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = PRICING.get(model, (0.0, 0.0))
    return (input_tokens / 1000) * in_price + (output_tokens / 1000) * out_price


def estimate_cost_inr(model: str, input_tokens: int, output_tokens: int) -> float:
    return estimate_cost_usd(model, input_tokens, output_tokens) * USD_TO_INR


def format_cost(usd: float) -> str:
    """Human-friendly cost string: '$0.0123 (₹1.02)'."""
    inr = usd * USD_TO_INR
    if usd == 0:
        return "free"
    if usd < 0.01:
        return f"${usd:.6f} (₹{inr:.4f})"
    return f"${usd:.4f} (₹{inr:.2f})"


@dataclass
class TurnUsage:
    """Snapshot of a single model turn's resource usage."""
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    tool_calls: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tokens_per_second(self) -> float:
        return self.output_tokens / self.duration_s if self.duration_s > 0 else 0.0


@dataclass
class SessionUsage:
    """Aggregate usage across a session (many turns)."""
    turns: list[TurnUsage] = field(default_factory=list)
    total_input: int = 0
    total_output: int = 0
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0
    total_tool_calls: int = 0

    def record(self, turn: TurnUsage) -> None:
        self.turns.append(turn)
        self.total_input += turn.input_tokens
        self.total_output += turn.output_tokens
        self.total_cost_usd += turn.cost_usd
        self.total_duration_s += turn.duration_s
        self.total_tool_calls += turn.tool_calls

    @property
    def total_tokens(self) -> int:
        return self.total_input + self.total_output

    @property
    def avg_tokens_per_turn(self) -> float:
        return self.total_tokens / len(self.turns) if self.turns else 0.0

    @property
    def avg_cost_per_turn(self) -> float:
        return self.total_cost_usd / len(self.turns) if self.turns else 0.0

    def summary(self) -> str:
        return (
            f"turns={len(self.turns)}  "
            f"in={self.total_input:,}  out={self.total_output:,}  "
            f"total={self.total_tokens:,}  "
            f"cost={format_cost(self.total_cost_usd)}  "
            f"tools={self.total_tool_calls}  "
            f"time={self.total_duration_s:.1f}s"
        )


class TokenCounter:
    """Thread-safe, persistent token counter — the single source of truth.

    The UI reads ``snapshot()`` every turn to render the live status bar.
    A rolling JSON log is written under ``~/.terminal_agent/usage.json`` so
    costs survive restarts and can be aggregated across sessions.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self.session = SessionUsage()
        self.goal_usage = SessionUsage()  # reset per goal run
        self.persist_path = persist_path or Path.home() / ".terminal_agent" / "usage.json"
        self._all_time_input = 0
        self._all_time_output = 0
        self._all_time_cost = 0.0
        self._all_time_turns = 0
        self._load()

    def _load(self) -> None:
        if not self.persist_path.exists():
            return
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
            self._all_time_input = data.get("total_input", 0)
            self._all_time_output = data.get("total_output", 0)
            self._all_time_cost = data.get("total_cost_usd", 0.0)
            self._all_time_turns = data.get("total_turns", 0)
        except (json.JSONDecodeError, OSError):
            pass

    def _persist(self) -> None:
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "total_input": self._all_time_input,
                "total_output": self._all_time_output,
                "total_cost_usd": self._all_time_cost,
                "total_turns": self._all_time_turns,
                "updated_at": time.time(),
            }
            self.persist_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def record_turn(
        self,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        duration_s: float = 0.0,
        tool_calls: int = 0,
        is_goal: bool = False,
    ) -> TurnUsage:
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        turn = TurnUsage(
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            duration_s=duration_s,
            tool_calls=tool_calls,
        )
        with self._lock:
            self.session.record(turn)
            if is_goal:
                self.goal_usage.record(turn)
            self._all_time_input += input_tokens
            self._all_time_output += output_tokens
            self._all_time_cost += cost
            self._all_time_turns += 1
            self._persist()
        return turn

    def reset_goal(self) -> None:
        with self._lock:
            self.goal_usage = SessionUsage()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_turns": len(self.session.turns),
                "session_input": self.session.total_input,
                "session_output": self.session.total_output,
                "session_total": self.session.total_tokens,
                "session_cost_usd": self.session.total_cost_usd,
                "session_cost_fmt": format_cost(self.session.total_cost_usd),
                "goal_turns": len(self.goal_usage.turns),
                "goal_input": self.goal_usage.total_input,
                "goal_output": self.goal_usage.total_output,
                "goal_total": self.goal_usage.total_tokens,
                "goal_cost_usd": self.goal_usage.total_cost_usd,
                "goal_cost_fmt": format_cost(self.goal_usage.total_cost_usd),
                "all_time_turns": self._all_time_turns,
                "all_time_total": self._all_time_input + self._all_time_output,
                "all_time_cost_usd": self._all_time_cost,
                "all_time_cost_fmt": format_cost(self._all_time_cost),
            }

    def last_turn(self) -> TurnUsage | None:
        with self._lock:
            return self.session.turns[-1] if self.session.turns else None

    def all_time_summary(self) -> str:
        return (
            f"all-time: turns={self._all_time_turns:,}  "
            f"tokens={self._all_time_input + self._all_time_output:,}  "
            f"cost={format_cost(self._all_time_cost)}"
        )


# Process-wide singleton. Imported lazily to avoid touching $HOME on import.
_counter: TokenCounter | None = None
_counter_lock = threading.Lock()


def get_token_counter() -> TokenCounter:
    global _counter
    if _counter is None:
        with _counter_lock:
            if _counter is None:
                _counter = TokenCounter()
    return _counter
