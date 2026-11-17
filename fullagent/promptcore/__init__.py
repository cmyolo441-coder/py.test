"""promptcore - deep attention engineering for long system prompts.

NOT enforcement. NOT security. Nothing blocks, filters or rewrites.
The FULL user prompt always travels to the model verbatim and intact.
This package makes the prompt impossible to lose attention on: maps
every instruction, grades its strength, distills layered digests and
weaves fresh recency anchors around the untouched prompt.

Modules: atlas, lexicon, salience, distill, echo, anchor, mirror,
weave, conductor. Deterministic, stdlib-only, hash-cached, zero
extra model calls."""

__version__ = "1.0.0"


def __getattr__(name):
    _map = {
        "Conductor": ("conductor", "Conductor"),
        "Atlas": ("atlas", "Atlas"),
        "build_atlas": ("atlas", "build_atlas"),
        "Directive": ("lexicon", "Directive"),
        "parse_directives": ("lexicon", "parse_directives"),
        "score": ("salience", "score"),
        "digest": ("distill", "digest"),
        "select_for_query": ("echo", "select_for_query"),
        "build_anchor": ("anchor", "build_anchor"),
        "reflect": ("mirror", "reflect"),
        "compose_wire": ("weave", "compose_wire"),
    }
    if name in _map:
        import importlib
        mod, attr = _map[name]
        return getattr(importlib.import_module("." + mod, __name__), attr)
    raise AttributeError(name)
