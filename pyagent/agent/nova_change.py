"""Nova change-impact forecasting."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .nova_similarity import SimilarityReport
from .nova_symbols import NovaSymbolGraph


@dataclass
class ChangeImpact:
    path: str
    score: int
    reasons: list[str] = field(default_factory=list)
    suggested_tests: list[str] = field(default_factory=list)


@dataclass
class ChangeForecast:
    impacts: list[ChangeImpact] = field(default_factory=list)
    scenarios: dict[str, list[str]] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        return {"impacts": len(self.impacts), "top": [(i.path, i.score) for i in self.impacts[:15]], "scenarios": self.scenarios}


def _test_guess(path: str) -> list[str]:
    stem = Path(path).stem
    guesses = [f"tests/test_{stem}.py", f"tests/{stem}_test.py"]
    if "/" in path:
        parent = Path(path).parent.name
        guesses.append(f"tests/test_{parent}.py")
    return guesses


def forecast_changes(graph: NovaSymbolGraph, similarity: SimilarityReport | None = None) -> ChangeForecast:
    symbols_by_file = Counter(s.path for s in graph.symbols)
    reverse_by_file: Counter[str] = Counter()
    for sym in graph.symbols:
        reverse_by_file[sym.path] += len(graph.reverse_calls.get(sym.qualname, []))
    import_by_file = {path: len(imps) for path, imps in graph.imports_by_file.items()}
    sim_by_file: Counter[str] = Counter()
    if similarity is not None:
        for a, b, score in similarity.pairs[:100]:
            sim_by_file[a] += int(score * 10)
            sim_by_file[b] += int(score * 10)
    impacts: list[ChangeImpact] = []
    for path in set(symbols_by_file) | set(import_by_file):
        score = symbols_by_file[path] * 2 + reverse_by_file[path] * 5 + import_by_file.get(path, 0) + sim_by_file[path]
        reasons: list[str] = []
        if symbols_by_file[path]:
            reasons.append(f"{symbols_by_file[path]} symbols")
        if reverse_by_file[path]:
            reasons.append(f"{reverse_by_file[path]} incoming calls")
        if import_by_file.get(path, 0):
            reasons.append(f"{import_by_file[path]} imports")
        if sim_by_file[path]:
            reasons.append("similar-file coupling")
        if score:
            impacts.append(ChangeImpact(path, int(score), reasons, _test_guess(path)))
    impacts.sort(key=lambda i: -i.score)
    return ChangeForecast(impacts=impacts[:80], scenarios=build_scenarios(impacts))


def build_scenarios(impacts: list[ChangeImpact]) -> dict[str, list[str]]:
    top = impacts[:20]
    return {
        "small_patch": [i.path for i in top[:3]],
        "medium_refactor": [i.path for i in top[:8]],
        "high_risk_review": [i.path for i in top if i.score >= 30][:12],
        "test_priority": [t for i in top[:8] for t in i.suggested_tests][:20],
    }


def forecast_for_paths(forecast: ChangeForecast, paths: list[str]) -> list[ChangeImpact]:
    wanted = set(paths)
    return [impact for impact in forecast.impacts if impact.path in wanted or Path(impact.path).stem in wanted]


def impact_context(forecast: ChangeForecast, limit: int = 12) -> str:
    lines = ["change-impact forecast:"]
    for item in forecast.impacts[:limit]:
        lines.append(f"- {item.score:>3} {item.path} ({'; '.join(item.reasons)})")
    return "\n".join(lines)
