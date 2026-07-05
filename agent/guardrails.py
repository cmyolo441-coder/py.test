"""Guardrails applied to tool calls before execution.

These run inside the agent loop as a defensive layer independent of the model:
they flag dangerous shell commands and enforce a per-turn tool-call budget so a
misbehaving model cannot loop forever or run something catastrophic silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from .utils.security import assess_command


@dataclass
class GuardDecision:
    allow: bool
    reason: str = ""
    force_confirm: bool = False


class Guardrails:
    def __init__(self, max_calls_per_turn: int = 25) -> None:
        self.max_calls_per_turn = max_calls_per_turn
        self._calls_this_turn = 0

    def start_turn(self) -> None:
        self._calls_this_turn = 0

    def check(self, tool_name: str, arguments: dict) -> GuardDecision:
        self._calls_this_turn += 1
        if self._calls_this_turn > self.max_calls_per_turn:
            return GuardDecision(allow=False, reason="tool-call budget exceeded for this turn")

        if tool_name in {"run_shell", "run_python"}:
            command = str(arguments.get("command") or arguments.get("code") or "")
            dangerous, reason = assess_command(command)
            if dangerous:
                return GuardDecision(allow=True, force_confirm=True, reason=reason or "")

        return GuardDecision(allow=True)
