"""enforcex — System Prompt Compliance Enforcement Toolkit.

This package turns raw system-prompt text into structured, checkable
rules and keeps them in a durable, versioned store:

  ruleextract  — parse prompt text into Rule objects (multi-language,
                 markdown/heading aware), categorize, infer severity,
                 dedupe, digest and sign into a PromptSpec.
  rulestore    — persistent JSON store (atomic writes, lockfile),
                 schema-versioned, with spec versioning (diff), history
                 and export/import round-trips.

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
    "ruleextract",
    "rulestore",
    "Rule",
    "PromptSpec",
    "extract_rules",
    "rules_digest",
    "RuleStore",
    "diff_specs",
    "__version__",
]

__version__ = "1.0.0"


def __getattr__(name: str):
    """PEP 562 lazy attribute access — import siblings on demand."""
    if name in ("ruleextract", "rulestore", "telemetry", "dashboard",
                "forge", "probes", "fuzz", "repair"):
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
    for name in ("ruleextract", "rulestore"):
        try:
            mod = importlib.import_module(f".{name}", __name__)
        except Exception as exc:  # pragma: no cover - defensive
            print(f"ENFORCEx SKIP {name}: {exc}")
            continue
        fn = getattr(mod, "_self_test", None)
        if callable(fn):
            try:
                if fn() != 0:
                    failures += 1
            except AssertionError as exc:
                print(f"ENFORCEx FAIL {name}: {exc}")
                failures += 1
    if failures:
        return 1
    print("ENFORCEx PACKAGE SELF-TEST PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
