"""scoring — Weighted violation scoring, grading and drift tracking.

Pure functions + a small stateful drift tracker that turn a list of
violations (as produced by detect.run_all / responseanaly /
injection.scan, or by the gate's local fallbacks) into:

  * a per-category weighted penalty,
  * a total score in [0, 100] (100 = perfectly compliant),
  * a letter grade (A+ .. F),
  * a verdict (pass / warn / fail),
  * a drift signal (is compliance improving or degrading over time?).

Design notes
------------
* Violations are dicts: {"category": str, "severity": str,
  "rule_id": str, "evidence": str, "confidence": float, ...}.
  Unknown fields are tolerated; missing fields get defaults.
* Category weights encode how much each category *matters*; severity
  multipliers encode how much a violation *hurts*; confidence scales
  the penalty down for low-confidence detections.
* Diminishing penalties: the Nth violation in one category contributes
  less than the first, so a wall of small nits does not nuke a reply
  that is otherwise fine.
* Everything is persistable via plain dicts (Score.to_dict /
  DriftTracker.to_dict + from_dict). Thread-safe via a lock in the
  tracker. Stdlib only; never raises on malformed input.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "CATEGORY_WEIGHTS",
    "SEVERITY_MULTIPLIERS",
    "GRADE_THRESHOLDS",
    "Score",
    "normalize_violation",
    "score_violations",
    "DriftTracker",
]


# ── weight tables ────────────────────────────────────────────────────────

CATEGORY_WEIGHTS: Dict[str, float] = {
    "identity": 3.0,
    "security": 3.0,
    "injection": 3.0,
    "leak": 2.8,
    "content_policy": 2.5,
    "safety": 2.5,
    "format": 1.5,
    "process": 1.4,
    "factual": 1.3,
    "language": 1.2,
    "scope": 1.2,
    "tone": 1.0,
    "style": 1.0,
    "other": 1.0,
}

SEVERITY_MULTIPLIERS: Dict[str, float] = {
    "critical": 3.0,
    "high": 2.0,
    "medium": 1.2,
    "low": 0.6,
    "info": 0.2,
}

GRADE_THRESHOLDS: Sequence[tuple[float, str]] = (
    (97.0, "A+"),
    (92.0, "A"),
    (85.0, "B"),
    (75.0, "C"),
    (60.0, "D"),
    (40.0, "E"),
    (0.0, "F"),
)

_DEFAULT_SEVERITY = "medium"
_DEFAULT_CONFIDENCE = 1.0
# Penalty budget per category before diminishing returns kick in fully.
_CATEGORY_SATURATION = 12.0
# Diminishing-returns exponent (>1 = concave).
_DIMINISH_EXPONENT = 1.35


def _category_weight(category: str) -> float:
    key = (category or "other").strip().lower()
    return CATEGORY_WEIGHTS.get(key, CATEGORY_WEIGHTS["other"])


def _severity_multiplier(severity: str) -> float:
    key = (severity or _DEFAULT_SEVERITY).strip().lower()
    return SEVERITY_MULTIPLIERS.get(key, SEVERITY_MULTIPLIERS[_DEFAULT_SEVERITY])


# ── dataclasses ──────────────────────────────────────────────────────────

@dataclass
class Score:
    """Final compliance score for one response."""
    total: float                     # 0..100, higher = more compliant
    grade: str                       # "A+" .. "F"
    category_scores: Dict[str, float]  # category -> 0..100 sub-score
    violations_count: int
    drift: float = 0.0               # filled by DriftTracker.update()
    verdict: str = "pass"            # pass | warn | fail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": round(self.total, 2),
            "grade": self.grade,
            "category_scores": {k: round(v, 2) for k, v in self.category_scores.items()},
            "violations_count": self.violations_count,
            "drift": round(self.drift, 4),
            "verdict": self.verdict,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "Score":
        if not isinstance(d, dict):
            return cls(total=0.0, grade="F", category_scores={},
                       violations_count=0, verdict="fail")
        def _f(key: str, default: float) -> float:
            try:
                return float(d.get(key, default))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default
        try:
            vc = int(d.get("violations_count", 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            vc = 0
        cats = d.get("category_scores", {})
        return cls(
            total=_f("total", 0.0),
            grade=str(d.get("grade", "F")),
            category_scores=dict(cats) if isinstance(cats, dict) else {},
            violations_count=vc,
            drift=_f("drift", 0.0),
            verdict=str(d.get("verdict", "fail")),
        )


# ── normalization ────────────────────────────────────────────────────────

def normalize_violation(raw: Any) -> Dict[str, Any]:
    """Coerce anything violation-shaped into a normalized dict.

    Accepts dicts, (category, severity) tuples, or bare strings.
    Never raises.
    """
    out: Dict[str, Any] = {
        "category": "other",
        "severity": _DEFAULT_SEVERITY,
        "rule_id": "",
        "evidence": "",
        "confidence": _DEFAULT_CONFIDENCE,
        "message": "",
    }
    try:
        if isinstance(raw, dict):
            for key in ("category", "severity", "rule_id", "evidence",
                        "message", "kind", "type"):
                if key in raw and raw[key] is not None:
                    out[key] = str(raw[key])
            conf = raw.get("confidence")
            if isinstance(conf, str):
                try:
                    conf = float(conf)
                except (TypeError, ValueError):
                    conf = None
            if isinstance(conf, (int, float)) and 0.0 < float(conf) <= 1.0:
                out["confidence"] = float(conf)
            elif isinstance(conf, (int, float)):
                out["confidence"] = min(1.0, max(0.1, float(conf)))
            if not out.get("message") and out.get("kind"):
                # A "kind" label (e.g. semantic_contradiction) is the
                # only message-like field some producers emit; the old
                # setdefault() never fired because "message" was
                # already present (as "") in the template.
                out["message"] = str(out["kind"])
        elif isinstance(raw, (tuple, list)) and len(raw) >= 1:
            out["category"] = str(raw[0])
            if len(raw) >= 2:
                out["severity"] = str(raw[1])
        elif isinstance(raw, str):
            low = raw.lower()
            for sev in ("critical", "high", "medium", "low"):
                if sev in low:
                    out["severity"] = sev
                    break
            out["category"] = low if low in CATEGORY_WEIGHTS else "other"
            out["message"] = raw
    except Exception:
        pass
    # canonical category/severity casing
    out["category"] = (out.get("category") or "other").strip().lower()
    sev = (out.get("severity") or _DEFAULT_SEVERITY).strip().lower()
    out["severity"] = sev if sev in SEVERITY_MULTIPLIERS else _DEFAULT_SEVERITY
    return out


# ── core scoring ─────────────────────────────────────────────────────────

def _grade_for(total: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if total >= threshold:
            return grade
    return "F"


def _verdict_for(total: float, violations: List[Dict[str, Any]],
                 rules: Optional[Any]) -> str:
    has_critical = any(v["severity"] == "critical" for v in violations)
    if has_critical or total < 40.0:
        return "fail"
    if total < 75.0 or any(v["severity"] == "high" for v in violations):
        return "warn"
    return "pass"


def score_violations(violations: Iterable[Any], rules: Optional[Any] = None) -> Score:
    """Compute a weighted Score from a list of raw violations.

    ``rules`` is optional context (a rule list/dict); it is tolerated
    but not required — scoring only needs the violations themselves.
    """
    normalized = [normalize_violation(v) for v in (violations or []) if v is not None]

    # group raw penalties per category
    per_category: Dict[str, List[float]] = {}
    for v in normalized:
        weight = _category_weight(v["category"])
        mult = _severity_multiplier(v["severity"])
        conf = float(v.get("confidence", 1.0) or 1.0)
        raw_penalty = 10.0 * weight * mult * conf
        per_category.setdefault(v["category"], []).append(raw_penalty)

    category_penalties: Dict[str, float] = {}
    for cat, penalties in per_category.items():
        penalties.sort(reverse=True)
        # diminishing penalties: sum(p_i / i) for i = 1..n, capped softly
        total = 0.0
        for i, p in enumerate(penalties, start=1):
            total += p / (i ** (1.0 / _DIMINISH_EXPONENT))
        # saturate: category can never eat more than _CATEGORY_SATURATION
        # * weight points off the total
        cap = _CATEGORY_SATURATION * _category_weight(cat)
        if total > cap:
            total = cap + (total - cap) * 0.25  # soft overshoot allowed
        category_penalties[cat] = total

    gross_penalty = sum(category_penalties.values())
    # convert gross penalty (unbounded-ish) into a 0..100 score via a
    # smooth curve: score = 100 * exp(-penalty / 60)
    total_score = 100.0 * math.exp(-gross_penalty / 60.0)
    total_score = max(0.0, min(100.0, total_score))

    category_scores: Dict[str, float] = {}
    all_cats = set(per_category.keys()) | {v["category"] for v in normalized}
    for cat in sorted(all_cats):
        pen = category_penalties.get(cat, 0.0)
        category_scores[cat] = max(0.0, min(100.0, 100.0 * math.exp(-pen / 30.0)))

    grade = _grade_for(total_score)
    verdict = _verdict_for(total_score, normalized, rules)
    return Score(
        total=total_score,
        grade=grade,
        category_scores=category_scores,
        violations_count=len(normalized),
        drift=0.0,
        verdict=verdict,
    )


# ── drift tracking ───────────────────────────────────────────────────────

class DriftTracker:
    """Exponential moving average of scores with trend direction.

    Usage::

        t = DriftTracker()
        t.update(score_a); t.update(score_b)
        print(t.summary())
    """

    def __init__(self, alpha: float = 0.3, window: int = 100) -> None:
        self._alpha = float(alpha) if 0.0 < float(alpha) <= 1.0 else 0.3
        self._window = max(10, int(window))
        self._ema: Optional[float] = None
        self._count = 0
        self._scores: List[float] = []
        self._last: Optional[Score] = None
        self._lock = threading.Lock()

    def update(self, score: Any) -> Score:
        """Feed a Score (or numeric total). Returns the updated Score
        with its ``drift`` field set to (ema_after - ema_before).
        Non-numeric input degrades to a no-op return instead of
        raising (the module promises never to raise on malformed
        input)."""
        try:
            value = float(getattr(score, "total", score))
        except (TypeError, ValueError):
            if isinstance(score, Score):
                return score
            return self._last or Score(total=0.0, grade="F",
                                       category_scores={},
                                       violations_count=0)
        if isinstance(value, float) and (value != value):
            value = 0.0
        with self._lock:
            before = self._ema
            if self._ema is None:
                self._ema = value
                drift = 0.0
            else:
                self._ema = self._alpha * value + (1.0 - self._alpha) * self._ema
                drift = (self._ema or 0.0) - (before or 0.0)
            self._count += 1
            self._scores.append(value)
            if len(self._scores) > self._window:
                self._scores = self._scores[-self._window:]
            if isinstance(score, Score):
                score.drift = drift
                self._last = score
                return score
            last = self._last or Score(total=value, grade=_grade_for(value),
                                       category_scores={}, violations_count=0)
            last.drift = drift
            self._last = last
            return last

    # -- introspection ----------------------------------------------------

    @property
    def ema(self) -> Optional[float]:
        with self._lock:
            return self._ema

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def trend(self) -> str:
        """One of: improving | degrading | stable | unknown."""
        with self._lock:
            if len(self._scores) < 3:
                return "unknown"
            half = max(1, len(self._scores) // 2)
            older = sum(self._scores[:-half]) / max(1, len(self._scores) - half)
            newer = sum(self._scores[-half:]) / half
            delta = newer - older
            if delta > 2.0:
                return "improving"
            if delta < -2.0:
                return "degrading"
            return "stable"

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            scores = list(self._scores)
        if not scores:
            return {"count": 0, "ema": None, "trend": "unknown",
                    "min": None, "max": None, "mean": None}
        half = max(1, len(scores) // 2)
        return {
            "count": self._count,
            "ema": round(self._ema, 2) if self._ema is not None else None,
            "trend": self.trend(),
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
            "mean": round(sum(scores) / len(scores), 2),
            "recent_mean": round(sum(scores[-half:]) / half, 2),
        }

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "alpha": self._alpha,
                "window": self._window,
                "ema": self._ema,
                "count": self._count,
                "scores": list(self._scores),
            }

    @classmethod
    def from_dict(cls, d: Any) -> "DriftTracker":
        if not isinstance(d, dict):
            return cls()
        try:
            alpha = float(d.get("alpha", 0.3))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            alpha = 0.3
        try:
            window = int(d.get("window", 100))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            window = 100
        tracker = cls(alpha=alpha, window=window)
        try:
            ema = d.get("ema")
            tracker._ema = float(ema) if ema is not None else None  # type: ignore[arg-type]
        except (TypeError, ValueError):
            tracker._ema = None
        try:
            tracker._count = int(d.get("count", 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            tracker._count = 0
        scores: List[float] = []
        for s in (d.get("scores", []) or []):
            try:
                scores.append(float(s))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        tracker._scores = scores
        return tracker


# ── explanations / breakdown ─────────────────────────────────────────────

@dataclass
class PenaltyLine:
    """One row of a human-readable penalty breakdown."""
    category: str
    severity: str
    rule_id: str
    penalty: float
    capped: bool
    detail: str


def penalty_breakdown(violations: Iterable[Any]) -> List[PenaltyLine]:
    """Explain exactly where the score went, line by line.

    Useful for reports and retry prompts: each row shows the raw
    weighted penalty for one violation and whether the per-category
    cap kicked in. Violations are listed biggest-penalty-first.
    """
    lines: List[PenaltyLine] = []
    per_category_totals: Dict[str, float] = {}
    ordered = [normalize_violation(v) for v in (violations or []) if v is not None]
    ordered.sort(
        key=lambda v: -(_severity_multiplier(v["severity"])
                        * _category_weight(v["category"])
                        * float(v.get("confidence", 1.0) or 1.0)),
    )
    for v in ordered:
        weight = _category_weight(v["category"])
        mult = _severity_multiplier(v["severity"])
        conf = float(v.get("confidence", 1.0) or 1.0)
        raw = 10.0 * weight * mult * conf
        cat = v["category"]
        per_category_totals[cat] = per_category_totals.get(cat, 0.0) + raw
        cap = _CATEGORY_SATURATION * weight
        capped = per_category_totals[cat] > cap
        detail = (
            f"weight={weight:.1f} x sev={mult:.1f} x conf={conf:.2f}"
            f" -> {raw:.2f} pts"
            + (" (category cap applied)" if capped else "")
        )
        lines.append(PenaltyLine(
            category=cat,
            severity=v["severity"],
            rule_id=str(v.get("rule_id", "")),
            penalty=round(raw, 2),
            capped=capped,
            detail=detail,
        ))
    return lines


def explain(score: Score, violations: Iterable[Any] = ()) -> str:
    """Short plain-English explanation of a Score."""
    parts: List[str] = [f"score {score.total:.1f}/100 (grade {score.grade}, "
                        f"verdict {score.verdict})"]
    if score.violations_count:
        parts.append(f"{score.violations_count} violation(s)")
    breakdown = penalty_breakdown(violations)
    if breakdown:
        worst = breakdown[0]
        parts.append(f"worst: {worst.category}/{worst.severity} "
                     f"({worst.penalty:.1f} pts)")
    if score.drift:
        direction = "up" if score.drift > 0 else "down"
        parts.append(f"drift {direction} {abs(score.drift):.1f}")
    return "; ".join(parts)


def merge_history(scores: Iterable[Any]) -> Dict[str, Any]:
    """Aggregate a sequence of Score objects (or dicts) into summary
    statistics suitable for persistence alongside a DriftTracker."""
    totals: List[float] = []
    grades: Dict[str, int] = {}
    verdicts: Dict[str, int] = {}
    for item in (scores or []):
        try:
            if isinstance(item, dict):
                total = item.get("total")
                grade = item.get("grade", "?")
                verdict = item.get("verdict", "?")
            else:
                total = getattr(item, "total", None)
                grade = getattr(item, "grade", "?")
                verdict = getattr(item, "verdict", "?")
        except Exception:
            continue
        if isinstance(total, (int, float)):
            totals.append(float(total))
        grades[str(grade)] = grades.get(str(grade), 0) + 1
        verdicts[str(verdict)] = verdicts.get(str(verdict), 0) + 1
    if not totals:
        return {"count": 0}
    return {
        "count": len(totals),
        "min": round(min(totals), 2),
        "max": round(max(totals), 2),
        "mean": round(sum(totals) / len(totals), 2),
        "grades": grades,
        "verdicts": verdicts,
    }


# ── self-test ────────────────────────────────────────────────────────────

def _self_test() -> None:  # pragma: no cover
    # clean response
    s0 = score_violations([])
    assert s0.total > 99.0 and s0.grade in ("A+", "A") and s0.verdict == "pass", s0

    # one critical security violation should fail hard
    s1 = score_violations([
        {"category": "security", "severity": "critical",
         "rule_id": "SEC-1", "evidence": "ignored instructions"},
    ])
    assert s1.total < 80.0 and s1.verdict == "fail", s1

    # low-severity nits should only warn at worst
    s2 = score_violations([
        {"category": "tone", "severity": "low"},
        {"category": "format", "severity": "low"},
    ])
    assert s2.total > 70.0, s2

    # diminishing: 5 tiny violations < 1 critical in impact ordering
    nits = score_violations([
        {"category": "tone", "severity": "low"} for _ in range(5)
    ])
    hard = score_violations([{"category": "security", "severity": "critical"}])
    assert hard.total < nits.total

    # normalization of weird input
    v = normalize_violation(("security", "critical"))
    assert v["category"] == "security" and v["severity"] == "critical"
    v = normalize_violation(None)
    assert v["category"] == "other"

    # dict round-trip
    d = s1.to_dict()
    again = Score.from_dict(d)
    assert abs(again.total - s1.total) < 0.01 and again.grade == s1.grade

    # drift tracker
    t = DriftTracker()
    for total in (90.0, 85.0, 80.0, 70.0, 60.0):
        t.update(Score(total=total, grade="C", category_scores={},
                       violations_count=1, verdict="warn"))
    summ = t.summary()
    assert summ["count"] == 5 and summ["trend"] == "degrading", summ
    restored = DriftTracker.from_dict(t.to_dict())
    assert restored.count == 5 and restored.ema == t.ema
    t2 = DriftTracker()
    for total in (60.0, 70.0, 80.0, 90.0):
        t2.update(total)
    assert t2.summary()["trend"] == "improving"

    # breakdown + explain + merge
    lines = penalty_breakdown([
        {"category": "security", "severity": "critical", "rule_id": "S1"},
        {"category": "tone", "severity": "low"},
    ])
    assert lines[0].penalty >= lines[-1].penalty and lines[0].capped is True
    assert lines[-1].severity == "low" and lines[-1].capped is False
    text = explain(s1, [{"category": "security", "severity": "critical"}])
    assert "score" in text and "worst" in text
    hist = merge_history([s1.to_dict(), s2.to_dict()])
    assert hist["count"] == 2 and "mean" in hist
    assert merge_history([]) == {"count": 0}

    print("scoring._self_test: OK")


if __name__ == "__main__":  # pragma: no cover
    _self_test()
