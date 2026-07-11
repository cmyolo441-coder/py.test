"""Command-line argument parsing for launching the agent.

Supports selecting the provider/model, one-shot prompts (non-interactive), and
toggling animations, so the agent works both interactively and in scripts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass
class CliArgs:
    provider: str | None
    model: str | None
    prompt: str | None
    no_anim: bool
    auto: bool
    version: bool
    theme: str | None
    spinner: str | None


def parse_args(argv: list[str] | None = None) -> CliArgs:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="A terminal AI assistant for software engineering tasks.",
    )
    parser.add_argument("-p", "--provider", help="LLM provider (zen/openai/anthropic/gemini/...).")
    parser.add_argument("-m", "--model", help="Model name to use.")
    parser.add_argument("-c", "--prompt", help="Run a single prompt then exit (non-interactive).")
    parser.add_argument("--no-anim", action="store_true", help="Disable animations.")
    parser.add_argument("--auto", action="store_true", help="Auto-approve dangerous tools.")
    parser.add_argument("-t", "--theme", help="UI theme (neon/cyberpunk/pastel/matrix/solarized).")
    parser.add_argument("--spinner", help="Spinner style (braille/dots/moon/line/arc/star/bounce).")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    ns = parser.parse_args(argv)
    return CliArgs(
        provider=ns.provider,
        model=ns.model,
        prompt=ns.prompt,
        no_anim=ns.no_anim,
        auto=ns.auto,
        version=ns.version,
        theme=ns.theme,
        spinner=ns.spinner,
    )
