"""Cost tracker — aggregates per-session, per-goal and all-time spend.

A thin convenience layer over ``token_counter.TokenCounter`` that produces
human-readable cost dashboards for the ``/cost`` command. Also tracks spend
against a per-session budget (Config.cost_budget_usd) and warns when
approaching/exceeding it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .token_counter import (
    TokenCounter, get_token_counter, format_cost, USD_TO_INR,
)


@dataclass
class BudgetStatus:
    budget_usd: float
    spent_usd: float
    remaining_usd: float
    percent_used: float
    exceeded: bool
    warning: bool  # >80% used


class CostTracker:
    """Wraps TokenCounter with budget enforcement and dashboard formatting."""

    def __init__(self, counter: TokenCounter | None = None, budget_usd: float = 0.0) -> None:
        self.counter = counter or get_token_counter()
        self.budget_usd = budget_usd

    def budget_status(self) -> BudgetStatus:
        spent = self.counter.session.total_cost_usd
        if self.budget_usd <= 0:
            return BudgetStatus(0, spent, 0, 0, False, False)
        remaining = max(0.0, self.budget_usd - spent)
        pct = (spent / self.budget_usd) * 100
        return BudgetStatus(
            budget_usd=self.budget_usd,
            spent_usd=spent,
            remaining_usd=remaining,
            percent_used=pct,
            exceeded=spent >= self.budget_usd,
            warning=pct >= 80.0,
        )

    def dashboard(self) -> str:
        """A multi-section cost dashboard string for the /cost command."""
        snap = self.counter.snapshot()
        lines = [
            "╭─ Cost Dashboard ────────────────────────────────╮",
            "│                                                 │",
            f"│  This session:                                  │",
            f"│    turns:      {snap['session_turns']:>6}                              │",
            f"│    input:      {snap['session_input']:>6,} tokens                       │",
            f"│    output:     {snap['session_output']:>6,} tokens                       │",
            f"│    total:      {snap['session_total']:>6,} tokens                       │",
            f"│    cost:       {snap['session_cost_fmt']:<32}│",
            "│                                                 │",
        ]
        if snap["goal_turns"] > 0:
            lines += [
                "│  Current goal:                                  │",
                f"│    turns:      {snap['goal_turns']:>6}                              │",
                f"│    tokens:     {snap['goal_total']:>6,}                              │",
                f"│    cost:       {snap['goal_cost_fmt']:<32}│",
                "│                                                 │",
            ]
        lines += [
            "│  All-time:                                      │",
            f"│    turns:      {snap['all_time_turns']:>6}                              │",
            f"│    tokens:     {snap['all_time_total']:>6,}                              │",
            f"│    cost:       {snap['all_time_cost_fmt']:<32}│",
            "│                                                 │",
        ]
        if self.budget_usd > 0:
            st = self.budget_status()
            bar_len = 30
            filled = int(bar_len * st.percent_used / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            status = "EXCEEDED" if st.exceeded else ("WARNING" if st.warning else "OK")
            lines += [
                f"│  Budget:  ${self.budget_usd:.2f}  [{bar}] {st.percent_used:5.1f}%  {status}",
                f"│           spent={format_cost(st.spent_usd)}  remaining={format_cost(st.remaining_usd)}",
                "│                                                 │",
            ]
        lines.append("╰─────────────────────────────────────────────────╯")
        # Truncate any line over 50 chars after the border for clean rendering.
        return "\n".join(lines)

    def per_model_breakdown(self) -> str:
        """Group session turns by model and show per-model cost."""
        turns = self.counter.session.turns
        if not turns:
            return "No turns recorded yet."
        by_model: dict[str, dict[str, Any]] = {}
        for t in turns:
            key = f"{t.provider}/{t.model}"
            entry = by_model.setdefault(key, {"turns": 0, "input": 0, "output": 0, "cost": 0.0})
            entry["turns"] += 1
            entry["input"] += t.input_tokens
            entry["output"] += t.output_tokens
            entry["cost"] += t.cost_usd
        lines = ["Per-model breakdown:"]
        for key, e in sorted(by_model.items(), key=lambda x: -x[1]["cost"]):
            lines.append(
                f"  {key:<45}  turns={e['turns']:>3}  "
                f"tok={e['input'] + e['output']:>6,}  cost={format_cost(e['cost'])}"
            )
        return "\n".join(lines)


_tracker: CostTracker | None = None


def get_cost_tracker(budget_usd: float = 0.0) -> CostTracker:
    global _tracker
    if _tracker is None:
        _tracker = CostTracker(budget_usd=budget_usd)
    elif budget_usd > 0:
        _tracker.budget_usd = budget_usd
    return _tracker
