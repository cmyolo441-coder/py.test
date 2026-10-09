"""Configuration: providers, models, effort levels, paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "FullAgent"


def _pick_app_dir() -> Path:
    """Choose a WRITABLE home for app state — never a crash path.

    Order: $FULLAGENT_HOME, ~/.fullagent, <tmp>/fullagent-<uid>.
    Each candidate is probed with a real write; the first one that
    actually works wins, so a read-only home dir degrades gracefully
    instead of killing the app at startup (OSError on event log)."""
    import tempfile
    candidates: list[Path] = []
    env = os.environ.get("FULLAGENT_HOME")
    if env:
        candidates.append(Path(env))
    candidates.append(Path.home() / ".fullagent")
    uid = str(os.getuid()) if hasattr(os, "getuid") else "user"
    candidates.append(Path(tempfile.gettempdir()) / f"fullagent-{uid}")
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".write-probe"
            probe.write_text("ok")
            probe.unlink()
            return c
        except OSError:
            continue
    # last resort: per-user tmp subdir so two users on the same box don't
    # clobber each other's config / event log / sessions
    fallback = Path(tempfile.gettempdir()) / f"fullagent-{uid}"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except OSError:
        return Path(tempfile.gettempdir())


APP_DIR = _pick_app_dir()
CONFIG_FILE = APP_DIR / "config.json"
HISTORY_FILE = APP_DIR / "history"
SESSIONS_DIR = APP_DIR / "sessions"
EVENT_LOG_FILE = APP_DIR / "eventlog.jsonl"

DEFAULT_TIMEOUT = 300.0
MAX_TOOL_ITERATIONS = 200
# PERF: hard cap on executed tool calls per turn — 200 iterations of LLM
# round-trips at ~2.5s each was the 500s+ hang. 25 calls is plenty for a
# single turn; the model can say "continue".
MAX_TOOL_CALLS_PER_TURN = 25
# PERF: stop early when the same tool call returns an identical result
# this many times in a row (no-progress loop).
NO_PROGRESS_STALL_LIMIT = 3
MAX_TOOL_OUTPUT_CHARS = 24_000
# One output ceiling for every effort level: 200k tokens.
MAX_TOKENS = 200_000
# Backends reject a request when input + max_tokens exceeds the model's
# context window. Every request's max_tokens is clamped to fit (client.py).
DEFAULT_CONTEXT_WINDOW = 262_144


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    base_url: str
    api_key: str
    color: str


@dataclass(frozen=True)
class Model:
    id: str
    provider: str
    label: str
    tag: str = ""
    tag_color: str = "grey62"
    supports_tools: bool = True
    supports_reasoning: bool = False
    # Total context window (input + output tokens). Used to clamp max_tokens
    # at send time so a request is never rejected for exceeding the window.
    context_window: int = DEFAULT_CONTEXT_WINDOW


def _provider_api_key(provider: str) -> str:
    # Priority: env var > key file > embedded fallback. An empty or
    # whitespace-only value does NOT count as "present" — it falls
    # through to the next source instead of shadowing the embedded
    # zero-config key with a dead empty string (e.g. KIOS_API_KEY=""
    # left in a shell or docker env would otherwise silently break
    # authentication).
    key = os.environ.get(f"{provider.upper()}_API_KEY", "").strip()
    if key:
        return key
    try:
        key = (APP_DIR / f"{provider}_api_key").read_text(
            encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        # OSError: missing/unreadable file. UnicodeDecodeError: file
        # has non-UTF-8 bytes — must not propagate here, this runs at
        # module import and would kill the whole app at startup.
        key = ""
    if key:
        return key
    # Embedded fallback: zero-config — the key ships in the build so the
    # user never has to configure anything. Env var / key file above
    # still take precedence when present.
    return _EMBEDDED_KEYS.get(provider, "")


# Build-time embedded API keys (zero-config). These ship inside the
# wheel/binary. Override anytime via <PROVIDER>_API_KEY env var or
# ~/.fullagent/<provider>_api_key file.
_EMBEDDED_KEYS: dict[str, str] = {
    "opencode": "oc_sk_beca47bb4fdd_mMprAdOncMckiWrjDmlcfTM0sxN73fX7",
    "kios": "sk-cFXQ576lsIctpudkYD5lPniF5UgHGLy1nKeXDscCEvK1LMZV",
}


PROVIDERS: dict[str, Provider] = {
    "kilo": Provider(
        key="kilo",
        name="Kilo Code",
        base_url="https://api.kilo.ai/api/gateway",
        api_key=_provider_api_key("kilo"),
        color="#f1fa8c",
    ),
    "kios": Provider(
        key="kios",
        name="Kios API",
        base_url="https://kiosapi.com/v1",
        api_key=_provider_api_key("kios"),
        color="#8be9fd",
    ),
    "opencode": Provider(
        key="opencode",
        name="OpenCode Zen",
        base_url="https://opencode.ai/zen/v1",
        api_key=_provider_api_key("opencode"),
        color="#ff79c6",
    ),
}

MODELS: list[Model] = [
    Model("stealth/union-alpha", "kilo", "Union Alpha",
          supports_tools=True, context_window=262_144),
    Model("atria-dawn-preview", "kios", "Atria Dawn Preview",
          tag="preview", supports_tools=True),
    Model("step-5-preview-free", "kios", "Step 5 Preview Free",
          tag="free", supports_tools=True, context_window=262_144),
    Model("ling-3.1-flash", "kios", "Ling 3.1 Flash",
          supports_tools=True, context_window=262_144),
    Model("glyph-cluster", "kios", "Glyph Cluster",
          supports_tools=True, context_window=262_144),
    Model("space-bunny-free", "opencode", "Space Bunny Free",
          tag="free", supports_tools=True, context_window=262_144),
]

DEFAULT_MODEL_ID = "stealth/union-alpha"


@dataclass(frozen=True)
class Effort:
    key: str
    label: str
    color: str
    max_tokens: int | None
    temperature: float
    reasoning_effort: str | None
    description: str


EFFORTS: list[Effort] = [
    Effort("low", "LOW", "#6272a4", MAX_TOKENS, 0.2, None,
           "short answers, minimal tokens"),
    Effort("medium", "MEDIUM", "#8be9fd", MAX_TOKENS, 0.4, None,
           "balanced length and speed"),
    Effort("high", "HIGH", "#50fa7b", MAX_TOKENS, 0.6, None,
           "thorough, detailed answers"),
    Effort("extrahigh", "EXTRA HIGH", "#ffb86c", MAX_TOKENS, 0.7, None,
           "deep work, long outputs"),
    Effort("ultrahigh", "ULTRA HIGH", "#ff5555", MAX_TOKENS, 0.8, None,
           "maximum depth, exhaustive work"),
]

DEFAULT_EFFORT = "high"


def model_by_id(model_id: str) -> Model | None:
    for m in MODELS:
        if m.id == model_id:
            return m
    return None


def effort_by_key(key: str) -> Effort | None:
    for e in EFFORTS:
        if e.key == key:
            return e
    return None


@dataclass
class Config:
    model_id: str = DEFAULT_MODEL_ID
    effort: str = DEFAULT_EFFORT
    auto_approve: bool = False
    show_reasoning: bool = False
    theme: str = "dracula"
    # which system prompt to send: "main" (compact) or "master" (130k+)
    prompt: str = "main"
    extra: dict = field(default_factory=dict)
    # PERF: sliding window for model-visible history. 0 = disabled (legacy).
    prune_window: int = 40

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if not isinstance(data, dict):
                data = {}
            for k in ("model_id", "effort", "auto_approve", "show_reasoning",
                      "theme", "prompt", "prune_window"):
                if k not in data:
                    continue
                if k in ("auto_approve", "show_reasoning"):
                    # safety gates must be real booleans — a drifted
                    # config with "auto_approve": "false" (truthy string)
                    # would silently disable the approval prompt
                    if isinstance(data[k], bool):
                        setattr(cfg, k, data[k])
                elif k == "prune_window":
                    # drift-safe int validation; 0 disables pruning
                    try:
                        cfg.prune_window = max(0, int(data[k]))
                    except (TypeError, ValueError):
                        pass
                else:
                    setattr(cfg, k, data[k])
            cfg.extra = {k: v for k, v in data.items()
                         if k not in ("model_id", "effort", "auto_approve",
                                      "show_reasoning", "theme", "prompt")}
        except (OSError, ValueError):
            pass
        if model_by_id(cfg.model_id) is None:
            cfg.model_id = DEFAULT_MODEL_ID
        if effort_by_key(cfg.effort) is None:
            cfg.effort = DEFAULT_EFFORT
        if not isinstance(cfg.prompt, str) or not cfg.prompt:
            cfg.prompt = "main"
        return cfg

    def save(self) -> None:
        try:
            ensure_dirs()
            data = {
                "model_id": self.model_id,
                "effort": self.effort,
                "auto_approve": self.auto_approve,
                "show_reasoning": self.show_reasoning,
                "theme": self.theme,
                "prompt": self.prompt,
            }
            data.update(self.extra)
            # atomic write: a crash mid-write must never leave truncated
            # JSON that would reset the whole config on next load
            tmp = CONFIG_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass  # config persistence is a convenience, never a crash path


def ensure_dirs() -> None:
    """Create every directory the app writes into. Called at startup AND
    before individual writes, so a deleted home dir heals itself."""
    for d in (APP_DIR, SESSIONS_DIR, APP_DIR / "memory",
              APP_DIR / "skills", APP_DIR / "store"):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def _self_check() -> None:
    """Verify config invariants. Run with: python3 -m fullagent.config"""
    assert PROVIDERS, "no providers defined"
    for key, p in PROVIDERS.items():
        assert key == p.key, f"provider dict key {key!r} != Provider.key {p.key!r}"
        assert p.base_url.startswith(("https://", "http://")), \
            f"provider {key!r} has bad base_url {p.base_url!r}"
    seen: set[str] = set()
    for m in MODELS:
        assert m.provider in PROVIDERS, \
            f"model {m.id!r} references unknown provider {m.provider!r}"
        assert m.id not in seen, f"duplicate model id {m.id!r}"
        seen.add(m.id)
        assert m.context_window > 0, f"model {m.id!r} has bad context_window"
    assert model_by_id(DEFAULT_MODEL_ID) is not None, \
        f"DEFAULT_MODEL_ID {DEFAULT_MODEL_ID!r} not in MODELS"
    assert effort_by_key(DEFAULT_EFFORT) is not None, \
        f"DEFAULT_EFFORT {DEFAULT_EFFORT!r} not in EFFORTS"
    # round-trip: save/load must preserve values and heal bad input
    cfg = Config.load()
    assert model_by_id(cfg.model_id) is not None
    assert effort_by_key(cfg.effort) is not None
    print(f"OK: {len(PROVIDERS)} providers, {len(MODELS)} models, "
          f"default={DEFAULT_MODEL_ID!r}/{DEFAULT_EFFORT!r}")


if __name__ == "__main__":
    _self_check()

