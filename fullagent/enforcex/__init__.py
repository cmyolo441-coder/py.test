"""enforcex — System Prompt Compliance Enforcement Toolkit.

Turns raw system-prompt text into structured, checkable rules and keeps
them in a durable, versioned store. The package has grown to ~40 submodules
organized in layers:

  Core parse/store          — ruleextract, rulestore
  Enforcement gate          — gate, scoring, responseanaly, detect, injection, ledger, seal
  Contracts & policy        — canon (PromptVault), policy, attestation
  Repair & adversarial      — repair, fuzz, mutate, probes, redteam, shadow
  Semantics & drift         — semantic, semantics, metaguard, critic, cognify, drift
  Streaming & consensus     — stream, consensus, orchestrator, warroom
  Observability & packing   — telemetry, dashboard, forge, report, cli, sandbox
  Obfuscation & multiturn    — cipherlab (decode), multiturn (coercion tracker)
  Trust & governance         — trust (adaptive trust scoring)
  Foresight & shield         — foresight (risk forecast), shield (few-shot shields)

Design rules inherited from the fullagent house style:
  * stdlib only, Python 3.9+,
  * dataclasses + small pure functions,
  * never raise on malformed input,
  * every module carries a `_self_test()` runnable via `python -m`.

Submodules are imported lazily so a missing/broken sibling never makes
the package itself un-importable.
"""

from __future__ import annotations

__all__ = [
    "ruleextract", "rulestore", "Rule", "PromptSpec",
    "extract_rules", "rules_digest", "RuleStore", "diff_specs",
    "gate", "scoring", "responseanaly", "detect", "injection",
    "ledger", "seal", "canon", "policy", "attestation",
    "repair", "fuzz", "mutate", "probes", "redteam", "shadow",
    "semantic", "semantics", "metaguard", "critic", "cognify", "drift",
    "stream", "consensus", "orchestrator", "warroom",
    "telemetry", "dashboard", "forge", "report", "cli", "sandbox",
    "cipherlab", "multiturn", "trust", "foresight", "shield",
    "__version__",
]

__version__ = "2.0.0"

_LAZY_MODULES = (
    "ruleextract", "rulestore", "telemetry", "dashboard",
    "forge", "probes", "fuzz", "repair", "gate", "scoring",
    "responseanaly", "detect", "injection", "ledger", "seal",
    "canon", "policy", "attestation", "mutate", "redteam", "shadow",
    "semantic", "semantics", "metaguard", "critic", "cognify", "drift",
    "stream", "consensus", "orchestrator", "warroom", "report", "cli",
    "sandbox", "cipherlab", "multiturn", "trust", "foresight", "shield",
)


def __getattr__(name: str):
    """PEP 562 lazy attribute access — import siblings on demand."""
    if name in _LAZY_MODULES:
        import importlib
        return importlib.import_module(f".{name}", __name__)
    if name in ("Rule", "PromptSpec", "extract_rules", "rules_digest"):
        from . import ruleextract as _m
        return getattr(_m, name)
    if name in ("RuleStore", "diff_specs"):
        from . import rulestore as _m
        return getattr(_m, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _self_test() -> int:
    """Run every sibling's self-test; a missing sibling is skipped."""
    import importlib
    failures = 0
    for name in _LAZY_MODULES:
        try:
            mod = importlib.import_module(f".{name}", __name__)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"ENFORCEx SKIP {name}: {exc}")
            continue
        fn = getattr(mod, "_self_test", None)
        if callable(fn):
            try:
                rv = fn()
                if rv is not None and rv != 0:
                    failures += 1
            except AssertionError as exc:
                print(f"ENFORCEx FAIL {name}: {exc}")
                failures += 1
            except Exception as exc:
                print(f"ENFORCEx FAIL {name} ({type(exc).__name__}): {exc}")
                failures += 1
    if failures:
        print(f"ENFORCEx PACKAGE SELF-TEST FAIL ({failures} modules failed)")
        return 1
    print("ENFORCEx PACKAGE SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
