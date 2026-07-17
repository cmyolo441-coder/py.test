#!/usr/bin/env python3
"""Entry point for the Terminal AI Agent."""

from __future__ import annotations

import sys

from agent import __version__
from agent.app import App
from agent.cli import parse_args


def main() -> None:
    args = parse_args()
    if args.version:
        print(f"terminal-agent {__version__}")
        return

    app = App(animations=not args.no_anim)
    if args.theme:
        from agent import themes
        if not themes.set_theme(args.theme):
            print(f"Unknown theme '{args.theme}'. Options: {', '.join(themes.names())}")
    if args.provider:
        app.config.provider = args.provider
        app.config.model = None
    if args.model:
        app.config.model = args.model
    if args.auto:
        app.config.auto_approve_tools = True

    if args.prompt:
        app.run_once(args.prompt)
    else:
        app.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
