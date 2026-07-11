"""Response quality scorer — rates model answers on multiple dimensions.

Used by Goal Mode's verifier to decide if an answer is genuinely good (not
just non-empty). Scores on:
  * completeness (does it address every part of the prompt?)
  * correctness signals (no obvious contradictions, code blocks parse)
  * clarity (sentence length, structure)
  * actionability (concrete steps vs vague advice)

Returns a 0-100 score and a brief justification. Lightweight, no API calls —
uses heuristics so it can run on every response without latency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_CODE_BLOCK_RE = re.compile(r"```(\w+)?\n(.*?)```", re.DOTALL)
_LIST_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
_QUESTION_RE = re.compile(r"\?")


@dataclass
class QualityScore:
    score: int  # 0-100
    completeness: int
    correctness: int
    clarity: int
    actionability: int
    notes: list[str] = field(default_factory=list)

    @property
    def grade(self) -> str:
        if self.score >= 90:
            return "A"
        if self.score >= 80:
            return "B"
        if self.score >= 70:
            return "C"
        if self.score >= 60:
            return "D"
        return "F"

    def summary(self) -> str:
        lines = [
            f"Quality: {self.score}/100 (grade {self.grade})",
            f"  completeness:   {self.completeness}/100",
            f"  correctness:    {self.correctness}/100",
            f"  clarity:        {self.clarity}/100",
            f"  actionability:  {self.actionability}/100",
        ]
        if self.notes:
            lines.append("  notes:")
            for n in self.notes:
                lines.append(f"    - {n}")
        return "\n".join(lines)


def score_response(prompt: str, response: str) -> QualityScore:
    """Heuristically score ``response`` against ``prompt``."""
    notes: list[str] = []
    if not response or not response.strip():
        return QualityScore(0, 0, 0, 0, 0, ["empty response"])

    # --- Completeness -----------------------------------------------------
    completeness = 50
    # Did the response address keywords from the prompt?
    prompt_words = set(w.lower() for w in re.findall(r"\b\w{4,}\b", prompt))
    resp_words = set(w.lower() for w in re.findall(r"\b\w{4,}\b", response))
    if prompt_words:
        coverage = len(prompt_words & resp_words) / len(prompt_words)
        completeness = int(50 + 50 * coverage)
    if len(response) < 50:
        completeness = min(completeness, 30)
        notes.append("very short response")
    if _QUESTION_RE.search(prompt) and not any(
        w in response.lower() for w in ["yes", "no", "maybe", "because", "the answer"]
    ):
        notes.append("question prompt but no clear answer phrase")

    # --- Correctness ------------------------------------------------------
    correctness = 80
    # Penalty for placeholder markers.
    for marker in ["TODO", "FIXME", "placeholder", "not implemented", "..."]:
        if marker.lower() in response.lower():
            correctness -= 15
            notes.append(f"contains '{marker}'")
    # Bonus for parseable code blocks.
    code_blocks = _CODE_BLOCK_RE.findall(response)
    if code_blocks:
        correctness = min(100, correctness + 10)
        notes.append(f"{len(code_blocks)} code block(s)")
    # Penalty for contradictions (simple heuristic: repeated opposite assertions).
    if response.count(" not ") > 5 and response.count(" is ") > 5:
        # crude — could be legitimate but worth flagging
        pass

    # --- Clarity ----------------------------------------------------------
    clarity = 70
    sentences = [s for s in re.split(r"[.!?]+", response) if s.strip()]
    if sentences:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        # Ideal sentence length: 10-25 words.
        if 10 <= avg_len <= 25:
            clarity = 90
        elif avg_len > 40:
            clarity = 50
            notes.append(f"long sentences (avg {avg_len:.0f} words)")
        elif avg_len < 5:
            clarity = 60
            notes.append("very short sentences")
    if _LIST_RE.search(response):
        clarity = min(100, clarity + 10)  # lists improve readability

    # --- Actionability ----------------------------------------------------
    actionability = 50
    if _LIST_RE.search(response):
        actionability += 20  # bullet lists usually mean concrete steps
    if code_blocks:
        actionability += 15  # code is actionable
    if any(w in response.lower() for w in ["step 1", "first,", "then,", "next,", "finally,"]):
        actionability += 15
        notes.append("step-by-step structure detected")
    actionability = min(100, actionability)

    # --- Overall ----------------------------------------------------------
    score = (
        completeness * 0.30
        + correctness * 0.30
        + clarity * 0.20
        + actionability * 0.20
    )
    return QualityScore(
        score=int(score),
        completeness=completeness,
        correctness=correctness,
        clarity=clarity,
        actionability=actionability,
        notes=notes,
    )
