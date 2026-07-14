"""Per-model capability registry — the single source of truth for running any
model at its true full capacity via *real* API parameters (never system-prompt
tricks).

Every model — whether it is listed in the table below or a brand-new id the
agent has never seen — resolves to a :class:`ModelCapability`. Providers then
turn the user's requested ``max_tokens`` / ``temperature`` / thinking level into
the exact request kwargs the model's API will accept:

  * the correct *output-token parameter name* (``max_tokens`` for classic Chat
    Completions and Anthropic, ``max_completion_tokens`` for OpenAI reasoning
    models which reject ``max_tokens``);
  * the model's *real* maximum output ceiling (requests are clamped, so a model
    is driven to its genuine limit and never past a value the API rejects);
  * real extended-thinking / reasoning parameters (Anthropic ``thinking`` +
    ``output_config.effort``, OpenAI ``reasoning_effort``, Gemini
    ``thinking_config`` via ``extra_body``);
  * a *temperature policy* — some reasoning models require ``temperature == 1``
    or reject the parameter entirely, so we omit it rather than trigger a 400.

The numbers here were gathered from current provider docs and adversarially
verified; where a documented value proved risky the *safe* (guaranteed-accept)
value is used and the nuance is kept in ``notes``. Unknown ids fall through
prefix/pattern rules to a conservative-but-correct profile, so an unrecognised
model still runs at a sane full capacity instead of erroring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any


class OutputParam(str, Enum):
    """Which request field caps output tokens for this model."""

    MAX_TOKENS = "max_tokens"                        # classic Chat Completions / Anthropic
    MAX_COMPLETION_TOKENS = "max_completion_tokens"  # OpenAI reasoning models


class ReasoningStyle(str, Enum):
    """How extended thinking / reasoning is requested for this model."""

    NONE = "none"
    ANTH_ADAPTIVE = "anthropic_adaptive"    # thinking={type:adaptive} + output_config.effort; NO budget_tokens
    ANTH_BUDGET = "anthropic_budget"        # thinking={type:enabled, budget_tokens:N}, 1024<=N<max_tokens
    OPENAI_EFFORT = "openai_effort"         # reasoning_effort: <levels>
    GEMINI_EFFORT = "gemini_effort"         # reasoning_effort OR extra_body google.thinking_config
    DEEPSEEK_IMPLICIT = "deepseek_implicit" # auto CoT, no input param; reasoning_content returned


class TempPolicy(str, Enum):
    """What the model does with the ``temperature`` parameter."""

    OMIT = "omit"                       # param removed; any value -> 400 (Opus 4.7/4.8)
    FIXED_ONE = "fixed_one"             # only temperature==1 accepted (OpenAI reasoning models)
    ONE_IF_THINKING = "one_if_thinking" # free normally, MUST be 1 when thinking enabled
    FREE = "free"                       # any value within [temp_min, temp_max]
    IGNORED = "ignored"                 # accepted but silently ignored (deepseek-reasoner)


class Status(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"  # callable now, retiring
    RETIRED = "retired"        # 404 — kept for migration UX only


@dataclass(frozen=True)
class ModelCapability:
    canonical_id: str
    provider: str
    context_window: int
    max_output_tokens: int
    output_param: OutputParam
    supports_reasoning: bool
    reasoning_style: ReasoningStyle = ReasoningStyle.NONE
    reasoning_effort_levels: tuple[str, ...] = ()
    reasoning_default_effort: str | None = None
    reasoning_min_budget: int = 0
    reasoning_max_budget: int = 0
    temp_policy: TempPolicy = TempPolicy.FREE
    temp_min: float = 0.0
    temp_max: float = 2.0
    temp_default: float = 1.0
    supports_verbosity: bool = False
    reasoning_mode_pro: bool = False
    status: Status = Status.ACTIVE
    aliases: tuple[str, ...] = ()
    replacement: str | None = None
    notes: str = ""
    source: str = "verified"


# ---------------------------------------------------------------------------
# Verified per-model table (see module docstring for provenance).
# ---------------------------------------------------------------------------

def _anth(canonical: str, ctx: int, out: int, **kw: Any) -> ModelCapability:
    kw.setdefault("output_param", OutputParam.MAX_TOKENS)
    return ModelCapability(canonical, "anthropic", ctx, out, **kw)


def _openai(canonical: str, ctx: int, out: int, **kw: Any) -> ModelCapability:
    return ModelCapability(canonical, "openai", ctx, out, **kw)


def _compat(canonical: str, provider: str, ctx: int, out: int, **kw: Any) -> ModelCapability:
    kw.setdefault("output_param", OutputParam.MAX_TOKENS)
    return ModelCapability(canonical, provider, ctx, out, **kw)


_TABLE: list[ModelCapability] = [
    # --- Anthropic Claude -------------------------------------------------
    _anth(
        "claude-opus-4-8", 1_000_000, 128_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_ADAPTIVE,
        reasoning_effort_levels=("low", "medium", "high", "xhigh", "max"),
        reasoning_default_effort="medium",
        temp_policy=TempPolicy.OMIT, temp_max=1.0,
        notes="Flagship. temperature/top_p/top_k all rejected (400). Streaming "
              "required above ~16K output (provider always streams). 300K output "
              "via Batches beta only.",
    ),
    _anth(
        "claude-opus-4-7", 1_000_000, 128_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_ADAPTIVE,
        reasoning_effort_levels=("low", "medium", "high", "xhigh", "max"),
        reasoning_default_effort="medium",
        temp_policy=TempPolicy.OMIT, temp_max=1.0,
        notes="Same request surface as 4.8.",
    ),
    _anth(
        "claude-opus-4-6", 1_000_000, 128_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_ADAPTIVE,
        reasoning_effort_levels=("low", "medium", "high", "max"),
        reasoning_default_effort="high",
        reasoning_min_budget=1024, reasoning_max_budget=127_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        notes="Sampling allowed (temp 0..1). Legacy budget_tokens still works.",
    ),
    _anth(
        "claude-sonnet-4-6", 1_000_000, 64_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_ADAPTIVE,
        reasoning_effort_levels=("low", "medium", "high"),
        reasoning_default_effort="high",
        reasoning_min_budget=1024, reasoning_max_budget=63_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        notes="No 'max' effort (Opus-tier only).",
    ),
    _anth(
        "claude-haiku-4-5", 200_000, 64_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=63_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        aliases=("claude-haiku-4-5-20251001",),
        notes="Budget-only thinking; the effort param errors on Haiku.",
    ),
    _anth(
        "claude-opus-4-5-20251101", 200_000, 64_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=63_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        aliases=("claude-opus-4-5",),
        notes="Adaptive thinking NOT supported; use extended-thinking budget_tokens.",
    ),
    _anth(
        "claude-opus-4-1-20250805", 200_000, 32_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=31_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        status=Status.DEPRECATED, aliases=("claude-opus-4-1",),
        replacement="claude-opus-4-8",
        notes="Deprecated; retires 2026-08-05.",
    ),
    _anth(
        "claude-sonnet-4-5-20250929", 200_000, 64_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=63_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        aliases=("claude-sonnet-4-5",),
        notes="Adaptive NOT available; effort errors. Budget-only.",
    ),
    _anth(
        "claude-opus-4-20250514", 200_000, 32_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=31_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        status=Status.RETIRED, aliases=("claude-opus-4-0",),
        replacement="claude-opus-4-8",
        notes="Retired 2026-06-15 (404).",
    ),
    _anth(
        "claude-sonnet-4-20250514", 200_000, 64_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=63_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        status=Status.RETIRED, aliases=("claude-sonnet-4-0",),
        replacement="claude-sonnet-4-6",
        notes="Retired 2026-06-15 (404).",
    ),
    _anth(
        "claude-3-7-sonnet-20250219", 200_000, 64_000,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=63_999,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
        status=Status.RETIRED, replacement="claude-sonnet-4-6",
        notes="Retired 2026-02-19 (404).",
    ),
    _anth(
        "claude-3-5-sonnet-20241022", 200_000, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0,
        status=Status.RETIRED, replacement="claude-sonnet-4-6",
        notes="Retired (404). No extended thinking.",
    ),
    _anth(
        "claude-3-5-haiku-20241022", 200_000, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0,
        status=Status.RETIRED, aliases=("claude-3-5-haiku",),
        replacement="claude-haiku-4-5",
        notes="Retired (404). No extended thinking.",
    ),
    _anth(
        "claude-3-opus-20240229", 200_000, 4_096,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0,
        status=Status.RETIRED, aliases=("claude-3-opus",),
        replacement="claude-opus-4-8",
        notes="Retired (404).",
    ),
    _anth(
        "claude-3-haiku-20240307", 200_000, 4_096,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0,
        status=Status.RETIRED, replacement="claude-haiku-4-5",
        notes="Retired 2026-04-20 (404).",
    ),

    # --- OpenAI -----------------------------------------------------------
    _openai(
        "gpt-4o", 128_000, 16_384, output_param=OutputParam.MAX_TOKENS,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Non-reasoning. gpt-4o-2024-05-13 snapshot caps output at 4096.",
    ),
    _openai(
        "gpt-4o-mini", 128_000, 16_384, output_param=OutputParam.MAX_TOKENS,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
    ),
    _openai(
        "gpt-4.1", 1_047_576, 32_768, output_param=OutputParam.MAX_TOKENS,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Smartest non-reasoning model; ~1M context.",
    ),
    _openai(
        "o1", 200_000, 100_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("low", "medium", "high"), reasoning_default_effort="medium",
        temp_policy=TempPolicy.FIXED_ONE, notes="Rejects max_tokens and sampling params.",
    ),
    _openai(
        "o3", 200_000, 100_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("low", "medium", "high"), reasoning_default_effort="medium",
        temp_policy=TempPolicy.FIXED_ONE,
    ),
    _openai(
        "o4-mini", 200_000, 100_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("low", "medium", "high"), reasoning_default_effort="medium",
        temp_policy=TempPolicy.FIXED_ONE,
    ),
    _openai(
        "gpt-5", 400_000, 128_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("minimal", "low", "medium", "high"),
        reasoning_default_effort="medium",
        temp_policy=TempPolicy.FIXED_ONE, supports_verbosity=True,
    ),
    _openai(
        "gpt-5.2", 400_000, 128_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("none", "low", "medium", "high", "xhigh"),
        reasoning_default_effort="none",
        temp_policy=TempPolicy.FIXED_ONE, supports_verbosity=True,
    ),
    _openai(
        "gpt-5.6", 1_050_000, 128_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("none", "low", "medium", "high", "xhigh", "max"),
        reasoning_default_effort="medium",
        temp_policy=TempPolicy.FIXED_ONE, supports_verbosity=True, reasoning_mode_pro=True,
        aliases=("gpt-5.6-sol", "gpt-5.6-terra"),
        notes=">272K input priced 2x.",
    ),

    # --- Gemini (OpenAI-compatible endpoint) ------------------------------
    _compat(
        "gemini-1.5-flash", "gemini", 1_000_000, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        status=Status.RETIRED, notes="Retired (404).",
    ),
    _compat(
        "gemini-1.5-pro", "gemini", 2_000_000, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        status=Status.RETIRED, notes="Retired (404).",
    ),
    _compat(
        "gemini-2.0-flash", "gemini", 1_048_576, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        status=Status.RETIRED, notes="Discontinued 2026-06-01.",
    ),
    _compat(
        "gemini-2.5-flash", "gemini", 1_048_576, 65_536,
        supports_reasoning=True, reasoning_style=ReasoningStyle.GEMINI_EFFORT,
        reasoning_effort_levels=("none", "minimal", "low", "medium", "high"),
        reasoning_default_effort="medium",
        reasoning_min_budget=0, reasoning_max_budget=24_576,
        temp_policy=TempPolicy.FREE,
        notes="Thinking on by default; 'none'/budget 0 disables.",
    ),
    _compat(
        "gemini-2.5-pro", "gemini", 1_048_576, 65_536,
        supports_reasoning=True, reasoning_style=ReasoningStyle.GEMINI_EFFORT,
        reasoning_effort_levels=("minimal", "low", "medium", "high"),
        reasoning_default_effort="high",
        reasoning_min_budget=128, reasoning_max_budget=24_576,
        temp_policy=TempPolicy.FREE,
        notes="Reasoning cannot be disabled. Safe budget cap 24576 (docs say 32768).",
    ),

    # --- OpenAI-compatible third-party ------------------------------------
    _compat(
        "llama-3.3-70b-versatile", "groq", 131_072, 32_768,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_default=1.0,
        status=Status.DEPRECATED, notes="Groq; deprecated 2026-06-17.",
    ),
    _compat(
        "mistral-large-latest", "mistral", 131_072, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_default=0.7,
        notes="Moving alias -> Large 3 (256K if pinned mistral-large-2512). Range 0-2.",
    ),
    _compat(
        "meta-llama/Llama-3.3-70B-Instruct-Turbo", "together", 131_072, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_default=0.7,
    ),
    _compat(
        "mimo-v2.5-free", "zen", 131_072, 8_192,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Zen stealth/free; specs may change. Query gateway /models at runtime.",
    ),
    _compat(
        "big-pickle", "zen", 200_000, 32_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Zen stealth/free (likely GLM-4.6). Treat as non-reasoning unless confirmed.",
    ),
    _compat(
        "deepseek-reasoner", "deepseek", 65_536, 8_192,
        supports_reasoning=True, reasoning_style=ReasoningStyle.DEEPSEEK_IMPLICIT,
        reasoning_max_budget=32_768, temp_policy=TempPolicy.IGNORED,
        status=Status.DEPRECATED, replacement="deepseek-v4-flash",
        notes="Auto CoT in reasoning_content; sampling params ignored. Output hard cap 8192. "
              "Deprecates 2026-07-24.",
    ),
    _compat(
        "MiniMax-M2.7", "sambanova", 128_000, 16_384,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="SambaNova Cloud OpenAI-compatible endpoint.",
    ),
    _compat(
        "agnes-2.0-flash", "agnes", 128_000, 16_384,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Agnes AI API Hub OpenAI-compatible endpoint.",
    ),
    _compat(
        "agnes-2.5-flash", "agnes", 128_000, 16_384,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Agnes AI API Hub OpenAI-compatible endpoint.",
    ),
    _compat(
        "agnes-2.5-pro", "agnes", 128_000, 32_768,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="Agnes AI API Hub OpenAI-compatible endpoint.",
    ),

    # --- NVIDIA API (nvapi) --------------------------------------------------
    _compat(
        "z-ai/glm-5.2", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: z-ai GLM 5.2",
    ),
    _compat(
        "minimaxai/minimax-m3", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: MiniMax M3 (may not respond)",
    ),
    _compat(
        "stepfun-ai/step-3.7-flash", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: StepFun Step 3.7 Flash",
    ),
    _compat(
        "deepseek-ai/deepseek-v4-pro", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: DeepSeek V4 Pro",
    ),
    _compat(
        "qwen/qwen3.5-397b-a17b", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: Qwen 3.5 397B-A17B",
    ),
    _compat(
        "mistralai/mistral-large-3-675b-instruct-2512", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: Mistral Large 3 675B",
    ),
    _compat(
        "nvidia/nemotron-3-ultra-550b-a55b", "nvapi", 200_000, 200_000,
        supports_reasoning=False, temp_policy=TempPolicy.FREE,
        notes="NVIDIA integrate: Nemotron 3 Ultra 550B",
    ),
]


# ---------------------------------------------------------------------------
# Registry / alias maps built at import time.
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ModelCapability] = {c.canonical_id: c for c in _TABLE}
_ALIASES: dict[str, str] = {}
for _cap in _TABLE:
    _ALIASES[_cap.canonical_id.lower()] = _cap.canonical_id
    for _alias in _cap.aliases:
        _ALIASES[_alias.lower()] = _cap.canonical_id


# ---------------------------------------------------------------------------
# Prefix / pattern fallback rules for unknown ids.
# Each rule: (compiled regex against lowercased id, builder(id, provider) -> cap).
# Order matters — most specific first.
# ---------------------------------------------------------------------------

def _mk(model_id: str, provider: str, ctx: int, out: int, **kw: Any) -> ModelCapability:
    kw.setdefault("source", "prefix-fallback")
    return ModelCapability(model_id, provider, ctx, out, **kw)


def _fb_gpt5(mid: str, prov: str) -> ModelCapability:
    ctx, levels, default, mode_pro = 400_000, ("minimal", "low", "medium", "high"), "medium", False
    if re.match(r"gpt-5\.(4|5|6|7|8|9)", mid):
        ctx, mode_pro = 1_050_000, True
        levels = ("none", "low", "medium", "high", "xhigh", "max")
    elif re.match(r"gpt-5\.(2|3)", mid):
        levels, default = ("none", "low", "medium", "high", "xhigh"), "none"
    return _mk(
        mid, prov, ctx, 128_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=levels, reasoning_default_effort=default,
        temp_policy=TempPolicy.FIXED_ONE, supports_verbosity=True, reasoning_mode_pro=mode_pro,
    )


def _fb_o_series(mid: str, prov: str) -> ModelCapability:
    return _mk(
        mid, prov, 200_000, 100_000, output_param=OutputParam.MAX_COMPLETION_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.OPENAI_EFFORT,
        reasoning_effort_levels=("low", "medium", "high"), reasoning_default_effort="medium",
        temp_policy=TempPolicy.FIXED_ONE,
    )


def _fb_claude_opus4_late(mid: str, prov: str) -> ModelCapability:
    # opus 4.7+ dropped the temperature knob and use adaptive thinking w/o budget.
    minor = int(re.search(r"claude-opus-4-(\d)", mid).group(1))
    omit = minor >= 7
    return _mk(
        mid, "anthropic", 1_000_000, 128_000, output_param=OutputParam.MAX_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_ADAPTIVE,
        reasoning_effort_levels=("low", "medium", "high", "xhigh", "max"),
        reasoning_default_effort="medium",
        reasoning_min_budget=0 if omit else 1024,
        reasoning_max_budget=0 if omit else 127_999,
        temp_policy=TempPolicy.OMIT if omit else TempPolicy.ONE_IF_THINKING, temp_max=1.0,
    )


def _fb_claude4(mid: str, prov: str) -> ModelCapability:
    is_opus = "opus" in mid
    out = 128_000 if is_opus else 64_000
    return _mk(
        mid, "anthropic", 1_000_000 if is_opus else 200_000, out,
        output_param=OutputParam.MAX_TOKENS,
        supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET,
        reasoning_min_budget=1024, reasoning_max_budget=out - 1,
        temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0,
    )


_PATTERNS: list[tuple[re.Pattern, Any]] = [
    # OpenAI reasoning families.
    (re.compile(r"^gpt-5"), _fb_gpt5),
    (re.compile(r"^o[1-9]"), _fb_o_series),
    # OpenAI non-reasoning.
    (re.compile(r"^gpt-4o-mini"), lambda m, p: _mk(m, p, 128_000, 16_384, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gpt-4o"), lambda m, p: _mk(m, p, 128_000, 16_384, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gpt-4\.1"), lambda m, p: _mk(m, p, 1_047_576, 32_768, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gpt-4"), lambda m, p: _mk(m, p, 128_000, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gpt-"), lambda m, p: _mk(m, p, 128_000, 16_384, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    # Anthropic.
    (re.compile(r"^claude-opus-4-[789]"), _fb_claude_opus4_late),
    (re.compile(r"^claude-3-5"), lambda m, p: _mk(m, "anthropic", 200_000, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0)),
    (re.compile(r"^claude-3-7"), lambda m, p: _mk(m, "anthropic", 200_000, 64_000, output_param=OutputParam.MAX_TOKENS, supports_reasoning=True, reasoning_style=ReasoningStyle.ANTH_BUDGET, reasoning_min_budget=1024, reasoning_max_budget=63_999, temp_policy=TempPolicy.ONE_IF_THINKING, temp_max=1.0)),
    (re.compile(r"^claude-3"), lambda m, p: _mk(m, "anthropic", 200_000, 4_096, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0)),
    (re.compile(r"^claude-(sonnet|opus|haiku)-4"), _fb_claude4),
    (re.compile(r"^claude-"), lambda m, p: _mk(m, "anthropic", 200_000, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_max=1.0)),
    # Gemini.
    (re.compile(r"^gemini-2\.5"), lambda m, p: _mk(m, "gemini", 1_048_576, 65_536, output_param=OutputParam.MAX_TOKENS, supports_reasoning=True, reasoning_style=ReasoningStyle.GEMINI_EFFORT, reasoning_effort_levels=(("minimal", "low", "medium", "high") if "-pro" in m else ("none", "minimal", "low", "medium", "high")), reasoning_default_effort="medium", reasoning_min_budget=128, reasoning_max_budget=24_576, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gemini-2"), lambda m, p: _mk(m, "gemini", 1_048_576, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gemini-1\.5"), lambda m, p: _mk(m, "gemini", 1_000_000, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"^gemini-"), lambda m, p: _mk(m, "gemini", 1_048_576, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    # Other OpenAI-compatible (substring matches, checked after prefixes).
    (re.compile(r"deepseek-(reasoner|r[0-9])"), lambda m, p: _mk(m, p or "deepseek", 65_536, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=True, reasoning_style=ReasoningStyle.DEEPSEEK_IMPLICIT, reasoning_max_budget=32_768, temp_policy=TempPolicy.IGNORED)),
    (re.compile(r"deepseek"), lambda m, p: _mk(m, p or "deepseek", 65_536, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"llama"), lambda m, p: _mk(m, p or "openai", 131_072, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE)),
    (re.compile(r"mistral|magistral|mixtral"), lambda m, p: _mk(m, p or "mistral", 131_072, 8_192, output_param=OutputParam.MAX_TOKENS, supports_reasoning=False, temp_policy=TempPolicy.FREE, temp_default=0.7)),
]


def _generic_default(model_id: str, provider: str | None) -> ModelCapability:
    """Maximally safe profile for a totally unknown model."""
    return ModelCapability(
        canonical_id=model_id, provider=provider or "unknown",
        context_window=8_192, max_output_tokens=4_096,
        output_param=OutputParam.MAX_TOKENS,
        supports_reasoning=False, reasoning_style=ReasoningStyle.NONE,
        temp_policy=TempPolicy.FREE, temp_min=0.0, temp_max=2.0, temp_default=1.0,
        status=Status.ACTIVE, source="generic-default",
        notes="Unknown model; conservative defaults. Query provider /models to widen.",
    )


def _normalize(model_id: str) -> str:
    mid = model_id.strip().lower()
    # Strip a leading vendor prefix used by gateways (openai/, google/) — but not
    # meta-llama/ which is part of Together's real id (handled by exact match first).
    prefixes = r"^(?:openai|google|anthropic|lovable)/"
    while re.search(prefixes, mid):
        mid = re.sub(prefixes, "", mid)
    mid = re.sub(r"-latest$", "", mid)
    return mid


@lru_cache(maxsize=512)
def resolve_capability(model_id: str, provider: str | None = None) -> ModelCapability:
    """Resolve ``model_id`` to a :class:`ModelCapability`.

    Order: exact canonical id -> alias -> normalized id/alias -> prefix/pattern
    rule -> generic conservative default. ``provider`` only fills ``cap.provider``
    on fallbacks (it never overrides a table entry's real provider).
    """
    if not model_id:
        return _generic_default(model_id, provider)

    # 1. Exact canonical (verbatim — keeps meta-llama/... ids intact).
    if model_id in _REGISTRY:
        return _REGISTRY[model_id]

    # 2. Alias / case-insensitive exact.
    canon = _ALIASES.get(model_id.lower())
    if canon:
        return _REGISTRY[canon]

    # 3. Normalized exact / alias.
    norm = _normalize(model_id)
    if norm in _REGISTRY:
        return _REGISTRY[norm]
    canon = _ALIASES.get(norm)
    if canon:
        return _REGISTRY[canon]

    # 4. Prefix / pattern rules.
    for pattern, builder in _PATTERNS:
        if pattern.search(norm):
            return builder(norm, provider)

    # 5. Generic default.
    return _generic_default(model_id, provider)


# ---------------------------------------------------------------------------
# Pure kwargs helpers consumed by providers.
# ---------------------------------------------------------------------------

def clamp_output_tokens(cap: ModelCapability, requested: int | None) -> int:
    """Clamp a requested output cap to the model's real ceiling (floor 1)."""
    n = cap.max_output_tokens if requested is None else requested
    return max(1, min(n, cap.max_output_tokens))


def output_kwargs(cap: ModelCapability, requested_max_tokens: int | None) -> dict[str, Any]:
    """``{"max_tokens": n}`` or ``{"max_completion_tokens": n}`` per the model."""
    return {cap.output_param.value: clamp_output_tokens(cap, requested_max_tokens)}


def temperature_kwargs(
    cap: ModelCapability, requested_temp: float | None, thinking_enabled: bool
) -> dict[str, Any]:
    """Return ``{"temperature": t}`` or ``{}`` per the model's policy.

    We *omit* temperature whenever a value would be rejected or silently
    ignored, rather than risk a 400.
    """
    p = cap.temp_policy
    if p in (TempPolicy.OMIT, TempPolicy.IGNORED, TempPolicy.FIXED_ONE):
        return {}
    if p is TempPolicy.ONE_IF_THINKING and thinking_enabled:
        return {"temperature": 1.0}
    t = cap.temp_default if requested_temp is None else requested_temp
    return {"temperature": max(cap.temp_min, min(t, cap.temp_max))}


# Map the agent's ThinkingLevel names to an ordered rank so we can snap an
# unsupported level to the nearest supported one.
_LEVEL_RANK = {
    "off": -1, "none": 0, "minimal": 1, "low": 2, "medium": 3,
    "high": 4, "xhigh": 5, "max": 6,
}


def normalize_effort(cap: ModelCapability, level: str | None) -> str | None:
    """Snap ``level`` (an agent ThinkingLevel) to a valid effort for this model.

    Returns ``None`` when reasoning should be off / is unsupported. Unsupported
    but higher levels snap *down* to the closest supported level (e.g. ``xhigh``
    -> ``high`` on Sonnet 4.6; ``max`` -> ``high`` on non-Opus).
    """
    if level is None or not cap.reasoning_effort_levels:
        return None
    level = level.lower()
    if level == "off":
        return "none" if "none" in cap.reasoning_effort_levels else None
    want = _LEVEL_RANK.get(level, 3)
    supported = sorted(cap.reasoning_effort_levels, key=lambda x: _LEVEL_RANK.get(x, 3))
    # Highest supported level that is <= want; else the lowest supported.
    best = None
    for lvl in supported:
        if _LEVEL_RANK.get(lvl, 3) <= want:
            best = lvl
    return best or supported[0]


def _budget_for(cap: ModelCapability, level: str | None) -> int:
    frac = {"minimal": 0.1, "low": 0.25, "medium": 0.5, "high": 1.0, "xhigh": 1.0, "max": 1.0}
    f = frac.get((level or "medium").lower(), 0.5)
    return int(cap.reasoning_max_budget * f)


def reasoning_kwargs(
    cap: ModelCapability,
    thinking_level: str | None,
    max_tokens: int,
    budget_override: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build reasoning request params.

    Returns ``(top_level_kwargs, extra_body)``. ``extra_body`` is non-empty only
    for the Gemini numeric-budget path. ``thinking_enabled`` for temperature
    purposes is ``bool(top_level_kwargs or extra_body)``.
    """
    off = thinking_level is None or str(thinking_level).lower() == "off"
    style = cap.reasoning_style

    if style in (ReasoningStyle.NONE, ReasoningStyle.DEEPSEEK_IMPLICIT):
        return {}, {}

    if style is ReasoningStyle.ANTH_ADAPTIVE:
        if off:
            return {}, {}
        effort = normalize_effort(cap, thinking_level) or cap.reasoning_default_effort or "medium"
        return {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": effort},
        }, {}

    if style is ReasoningStyle.ANTH_BUDGET:
        if off:
            return {}, {}
        ceiling = min(cap.reasoning_max_budget, max_tokens - 1)
        if ceiling < cap.reasoning_min_budget:
            # Output cap too small to fit any thinking budget — run without it.
            return {}, {}
        budget = budget_override if budget_override is not None else _budget_for(cap, thinking_level)
        budget = max(cap.reasoning_min_budget, min(budget, ceiling))
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}, {}

    if style is ReasoningStyle.OPENAI_EFFORT:
        if off:
            if "none" in cap.reasoning_effort_levels:
                return {"reasoning_effort": "none"}, {}
            return {}, {}  # no explicit off; default effort runs
        effort = normalize_effort(cap, thinking_level)
        return ({"reasoning_effort": effort} if effort else {}), {}

    if style is ReasoningStyle.GEMINI_EFFORT:
        if off:
            if "none" in cap.reasoning_effort_levels:
                return {"reasoning_effort": "none"}, {}
            return {}, {}
        if budget_override is not None:
            budget = max(cap.reasoning_min_budget, min(budget_override, cap.reasoning_max_budget))
            return {}, {"google": {"thinking_config": {"thinking_budget": budget, "include_thoughts": True}}}
        effort = normalize_effort(cap, thinking_level)
        return ({"reasoning_effort": effort} if effort else {}), {}

    return {}, {}


def replacement_for(cap: ModelCapability) -> str | None:
    """Suggested migration target for a retired/deprecated model, if any."""
    return cap.replacement
