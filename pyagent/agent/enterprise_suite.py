"""Enterprise feature activation suite.

This module keeps a single, inspectable source of truth for the "129 feature"
startup profile requested by power users.  The features are intentionally
metadata-only here; the actual implementations live across the existing agent
modules (tools, commands, UI, RAG, memory, security, orchestration, etc.).

`activate_enterprise_mode()` is called by App startup to make the complete
surface available by default while preserving safety gates for dangerous
operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EnterpriseFeature:
    """A named capability in the enterprise startup profile."""

    id: int
    category: str
    name: str
    description: str


# Exactly 129 visible enterprise capabilities.  Keep this list static so the
# boot screen and /features129 command are deterministic.
FEATURES_129: tuple[EnterpriseFeature, ...] = tuple(
    EnterpriseFeature(i + 1, category, name, description)
    for i, (category, name, description) in enumerate(
        [
            # UI / Prompt / Experience (1-18)
            ("UI", "Codex-style double-line prompt", "Input is typed inside a bordered terminal prompt box."),
            ("UI", "Right-edge prompt border", "The live prompt keeps a right-side border via prompt_toolkit rprompt."),
            ("UI", "Animated boot sequence", "Cinematic startup with subsystem readiness lines."),
            ("UI", "Dark Codex theme", "Low-glare dark palette matching modern coding CLIs."),
            ("UI", "Gradient title", "Animated Rich gradient heading for startup and banners."),
            ("UI", "Fuzzy slash menu", "Typo-tolerant command matching for all registered slash commands."),
            ("UI", "Command descriptions", "Completion dropdown includes command metadata."),
            ("UI", "History autosuggest", "Prompt suggestions are pulled from prior inputs."),
            ("UI", "Safe multiline paste", "Pasted blocks do not auto-submit by accident."),
            ("UI", "Alt+Enter force send", "Multiline prompt can be submitted intentionally."),
            ("UI", "Ctrl+Space palette", "Command menu opens on demand."),
            ("UI", "Ctrl+L prompt clear", "Current input buffer can be cleared instantly."),
            ("UI", "Mouse-friendly selection", "Native terminal copy remains available."),
            ("UI", "Live status bar", "Clock, provider, model, token and cost summary."),
            ("UI", "Animated thinking footer", "Spinner-based working indicator during reasoning."),
            ("UI", "Streaming markdown", "Responses render live with markdown formatting."),
            ("UI", "Syntax-highlighted code", "Code blocks render with line numbers and highlighting."),
            ("UI", "Theme hot swap", "Themes can be changed without restarting."),

            # Agent core / reasoning (19-36)
            ("Agent Core", "Tool-calling agent loop", "LLM tool calls execute over multiple reasoning rounds."),
            ("Agent Core", "Streaming cancellation", "ESC cancellation keeps partial output safely."),
            ("Agent Core", "Guardrail budget", "Per-turn tool-call limits prevent runaway loops."),
            ("Agent Core", "Dangerous command scan", "Shell commands are scanned before execution."),
            ("Agent Core", "Human approval gate", "Dangerous tools still request confirmation."),
            ("Agent Core", "Auto context compaction", "Large conversations are compacted automatically."),
            ("Agent Core", "Effort profiles", "Normal through godmode execution profiles are supported."),
            ("Agent Core", "Goal mode", "Persistent autonomous goal execution."),
            ("Agent Core", "Goal history", "Goal runs are tracked and resumable."),
            ("Agent Core", "Recovery checkpoint", "Session checkpoint is saved every turn."),
            ("Agent Core", "Conversation branching", "Named branches can fork/switch/delete history."),
            ("Agent Core", "Quality scoring", "Responses can be scored for completeness and correctness."),
            ("Agent Core", "Self-reflection", "Optional reflection loop improves answers."),
            ("Agent Core", "Smart model routing", "Prompt classification can pick a better model."),
            ("Agent Core", "Fallback chain", "Provider fallback keeps work moving on failures."),
            ("Agent Core", "Consensus mode", "Multiple models can be queried for cross-checking."),
            ("Agent Core", "Task analyzer", "Complex tasks can auto-orchestrate specialists."),
            ("Agent Core", "KV response cache", "Repeated prompts can be served from a local cache."),

            # Memory / Knowledge / RAG (37-52)
            ("Memory", "Long-term semantic memory", "Facts can persist beyond a session."),
            ("Memory", "Episodic memory", "Past interactions can be recalled."),
            ("Memory", "RAG v2 vector store", "Codebase chunks are searchable semantically."),
            ("Memory", "Index codebase command", "Project files can be indexed on demand."),
            ("Memory", "Knowledge graph builder", "AST graph captures files, imports, classes and functions."),
            ("Memory", "Knowledge graph search", "Entities can be queried by name and kind."),
            ("Memory", "Shortest graph path", "Relationships can be traced between nodes."),
            ("Memory", "SQLite knowledge store", "Requirements, decisions and rules persist locally."),
            ("Memory", "Markdown knowledge projection", "Human-readable KNOWLEDGE.md is generated."),
            ("Memory", "Project brain", "Project radar and next-action hints are injected."),
            ("Memory", "Context injector", "Relevant project state enriches prompts."),
            ("Memory", "Cache layer", "Reusable cache utilities reduce repeated work."),
            ("Memory", "Token-aware budgeting", "Context size is estimated per provider/model."),
            ("Memory", "Session export", "Chats export to markdown and JSON."),
            ("Memory", "Named snapshots", "Manual session snapshots can be written to disk."),
            ("Memory", "Prompt library", "Reusable system/task prompts are built in."),

            # Tools / Code / Files (53-75)
            ("Tools", "File read/write tools", "Agent can inspect and update workspace files."),
            ("Tools", "Search and grep tools", "Regex/text search across projects."),
            ("Tools", "Edit tools", "Targeted replacements and file editing helpers."),
            ("Tools", "Shell execution", "Safe shell commands can be run with guardrails."),
            ("Tools", "Python execution", "Python snippets can be evaluated in the sandbox."),
            ("Tools", "Git tools", "Status, diff, branch and commit helpers."),
            ("Tools", "HTTP tools", "Fetch APIs and web resources."),
            ("Tools", "Archive tools", "Zip/tar extraction and creation helpers."),
            ("Tools", "JSON tools", "Parse, validate and transform JSON."),
            ("Tools", "CSV tools", "Inspect and manipulate CSV data."),
            ("Tools", "Encoding tools", "Base64, hash and codec utilities."),
            ("Tools", "Text tools", "Summarise, transform and analyse text."),
            ("Tools", "Math tools", "Calculator and numeric utilities."),
            ("Tools", "Random tools", "IDs, passwords and random data helpers."),
            ("Tools", "System tools", "Platform and environment inspection."),
            ("Tools", "Network tools", "DNS, ping and port helpers where available."),
            ("Tools", "Process tools", "Inspect and manage processes safely."),
            ("Tools", "Color tools", "Palette and color conversion helpers."),
            ("Tools", "Unit conversion", "Common measurement conversions."),
            ("Tools", "Code structure analyzer", "AST-based function/class/import extraction."),
            ("Tools", "Complexity counter", "Cyclomatic complexity estimation."),
            ("Tools", "TODO scanner", "Find TODO/FIXME/HACK markers."),
            ("Tools", "Docstring extractor", "Generate documentation insight from code."),

            # Security / Compliance (76-91)
            ("Security", "SAST scanner", "Static checks for eval, injection and unsafe patterns."),
            ("Security", "Secret scanner", "Detect hardcoded keys and sensitive tokens."),
            ("Security", "Dependency scanner", "Spot risky pinned dependency versions."),
            ("Security", "PII scanner", "Emails, phones, cards, SSNs, Aadhaar and PAN detection."),
            ("Security", "Infrastructure scanner", "Dockerfile, Terraform and CloudFormation checks."),
            ("Security", "SBOM generator", "CycloneDX software bill of materials."),
            ("Security", "Immutable audit log", "Hash-chained local audit entries."),
            ("Security", "Audit verification", "Tampering detection for audit logs."),
            ("Security", "Sandbox policy", "Dangerous execution paths are isolated and confirmed."),
            ("Security", "PII redaction utilities", "Sensitive strings can be masked."),
            ("Security", "Auth helpers", "Authentication utilities are available."),
            ("Security", "Secrets manager", "Local secret handling helpers."),
            ("Security", "Supply-chain metadata", "SBOM summaries and dependency inventory."),
            ("Security", "Guardrail confirmations", "High-risk actions force user approval."),
            ("Security", "Vulnerability reports", "Security findings include severity metadata."),
            ("Security", "Compliance dashboard", "Security results can be summarized in commands."),

            # DevOps / Runtime (92-107)
            ("DevOps", "Dockerfile generator", "Production-ready Python/Node Dockerfiles."),
            ("DevOps", "Docker compose helpers", "Compose up/down/ps/log workflows."),
            ("DevOps", "CI/CD generator", "GitHub Actions, GitLab, CircleCI and Jenkins."),
            ("DevOps", "Git workflow automation", "Branch, commit, push and optional PR flow."),
            ("DevOps", "Cloud cost analysis", "Idle and right-sizing recommendations."),
            ("DevOps", "Prometheus exporter", "Metrics can be served for monitoring."),
            ("DevOps", "Telemetry collector", "Opt-in event collection and dashboard."),
            ("DevOps", "Profiler", "Timing breakdown per turn and aggregate."),
            ("DevOps", "Healthcheck script", "Offline subsystem verification."),
            ("DevOps", "Connection pool", "Shared HTTP client management."),
            ("DevOps", "Hot reload", "Agent modules can reload during development."),
            ("DevOps", "Scheduler", "Background recurring tasks."),
            ("DevOps", "MCP server", "Expose tools via Model Context Protocol."),
            ("DevOps", "Plugin loader", "User plugins from ~/.terminal_agent/plugins."),
            ("DevOps", "Plugin marketplace", "Install curated plugins."),
            ("DevOps", "Tool creator", "Generate and validate new tool code."),

            # Providers / Models (108-117)
            ("Providers", "OpenAI provider", "OpenAI-compatible chat backend."),
            ("Providers", "Anthropic provider", "Claude-family backend support."),
            ("Providers", "Gemini provider", "Google Gemini backend support."),
            ("Providers", "Mistral provider", "Mistral backend support."),
            ("Providers", "Together provider", "Together AI backend support."),
            ("Providers", "Groq provider", "Groq backend support."),
            ("Providers", "Ollama provider", "Local model backend."),
            ("Providers", "Zen provider", "Zero-config OpenAI-compatible provider."),
            ("Providers", "Zyloo provider", "Additional OpenAI-compatible model gateway."),
            ("Providers", "NVAPI provider", "NVIDIA-compatible model gateway."),

            # Automation / Frontier (118-129)
            ("Frontier", "Multi-agent specialists", "Planner, coder, reviewer, tester and more."),
            ("Frontier", "Parallel orchestration", "Run several specialists concurrently."),
            ("Frontier", "Browser automation", "Navigate pages and capture text/screenshots."),
            ("Frontier", "Vision analysis", "Describe images from file or URL."),
            ("Frontier", "Voice interface", "Text-to-speech and voice availability hooks."),
            ("Frontier", "Program synthesis", "Generate candidate functions from task descriptions."),
            ("Frontier", "Formal verifier", "Lightweight correctness checks where available."),
            ("Frontier", "Causal model", "Reasoning helpers for cause/effect analysis."),
            ("Frontier", "Counterfactual reasoning", "Explore alternative outcomes."),
            ("Frontier", "Theory of mind", "Persona/user-intent modelling helpers."),
            ("Frontier", "Self-evolving hooks", "Improvement loops and learning utilities."),
            ("Frontier", "MegaForge stack", "Project Brain, OMNI-AION, Titan, Atlas and ForgeCore integrations."),
        ]
    )
)

assert len(FEATURES_129) == 129, f"expected 129 features, got {len(FEATURES_129)}"


SAFE_RUNTIME_DEFAULTS: dict[str, Any] = {
    "enable_tools": True,
    "auto_orchestrate": True,
    "stream": True,
    "max_tool_iterations": 40,
    "max_tokens": 128000,
    "effort": "enterprise",
}


def feature_count() -> int:
    return len(FEATURES_129)


def combined_feature_count() -> int:
    """Return the total activated feature count (enterprise + hyper + apex)."""
    total = feature_count()
    try:
        from .hyper_suite import hyper_feature_count

        total += hyper_feature_count()
    except Exception:
        pass
    try:
        from .apex_suite import apex_feature_count

        total += apex_feature_count()
    except Exception:
        pass
    try:
        from .omega_suite import omega_feature_count

        total += omega_feature_count()
    except Exception:
        pass
    try:
        from .nova_suite import nova_feature_count

        total += nova_feature_count()
    except Exception:
        pass
    try:
        from .zenith_suite import zenith_feature_count

        total += zenith_feature_count()
    except Exception:
        pass
    return total


def by_category() -> dict[str, list[EnterpriseFeature]]:
    grouped: dict[str, list[EnterpriseFeature]] = {}
    for feature in FEATURES_129:
        grouped.setdefault(feature.category, []).append(feature)
    return grouped


def activate_enterprise_mode(app: Any | None = None) -> dict[str, Any]:
    """Activate the enterprise profile for the current process.

    This enables tools, orchestration and high iteration limits, and flips known
    feature flags in memory.  It deliberately does **not** auto-approve dangerous
    actions: safety confirmations remain active unless the user explicitly runs
    `/auto` or passes `--auto`.
    """
    summary = {
        "features": feature_count(),
        "combined_features": combined_feature_count(),
        "categories": len(by_category()),
        "flags_enabled": 0,
        "safety": "dangerous actions still require approval",
    }
    if app is None:
        return summary

    cfg = getattr(app, "config", None)
    if cfg is not None:
        for key, value in SAFE_RUNTIME_DEFAULTS.items():
            if hasattr(cfg, key):
                current = getattr(cfg, key)
                if isinstance(value, bool):
                    setattr(cfg, key, value)
                elif isinstance(value, int):
                    setattr(cfg, key, max(current, value) if isinstance(current, int) else value)
                else:
                    setattr(cfg, key, value)
        # Keep approval safe.  The user can still enable /auto explicitly.
        if hasattr(cfg, "auto_approve_tools") and getattr(cfg, "auto_approve_tools") is None:
            cfg.auto_approve_tools = False

    try:
        from .feature_flags import get_feature_flags

        flags = get_feature_flags()
        with flags._lock:  # noqa: SLF001 - runtime activation, no persistence
            for name in flags._flags:  # noqa: SLF001
                flags._flags[name] = True  # noqa: SLF001
            summary["flags_enabled"] = sum(1 for enabled in flags._flags.values() if enabled)  # noqa: SLF001
    except Exception:
        pass

    setattr(app, "enterprise_features", FEATURES_129)
    setattr(app, "enterprise_profile_active", True)
    try:
        from .hyper_suite import activate_hyper_mode

        summary["hyper"] = activate_hyper_mode(app)
    except Exception:
        pass
    try:
        from .apex_suite import activate_apex_mode

        summary["apex"] = activate_apex_mode(app)
    except Exception:
        pass
    try:
        from .omega_suite import activate_omega_mode

        summary["omega"] = activate_omega_mode(app)
    except Exception:
        pass
    try:
        from .nova_suite import activate_nova_mode

        summary["nova"] = activate_nova_mode(app)
    except Exception:
        pass
    try:
        from .zenith_suite import activate_zenith_mode

        summary["zenith"] = activate_zenith_mode(app)
    except Exception:
        pass
    summary["combined_features"] = combined_feature_count()
    return summary


def dashboard(limit: int | None = None) -> str:
    """Render a compact text dashboard for /features129 and startup."""
    grouped = by_category()
    lines = [
        "╔════════════════════════════════════════════════════════════╗",
        f"║  ENTERPRISE PROFILE: {feature_count()} FEATURES ACTIVE{'':<18}║",
        "╠════════════════════════════════════════════════════════════╣",
    ]
    for category, features in grouped.items():
        lines.append(f"║  {category:<12} {len(features):>3} capabilities{'':<29}║")
    lines.append("╚════════════════════════════════════════════════════════════╝")
    lines.append("")
    shown = 0
    for category, features in grouped.items():
        lines.append(f"[{category}]")
        for feature in features:
            if limit is not None and shown >= limit:
                lines.append(f"… {feature_count() - shown} more. Use /features129 all to show everything.")
                return "\n".join(lines)
            lines.append(f"  {feature.id:03d}. {feature.name} — {feature.description}")
            shown += 1
        lines.append("")
    return "\n".join(lines).rstrip()
