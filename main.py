#!/usr/bin/env python3
"""Entry point for the Advanced Terminal AI Agent.

Usage:
    python main.py                          # interactive
    python main.py -p zen -m big-pickle     # pick provider/model
    python main.py -c "what is 2+2?"        # one-shot prompt
    python main.py --no-anim                # disable animations
"""

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
    if args.spinner:
        from agent import effects

        if args.spinner in effects.SPINNERS:
            app.ui.spinner = args.spinner
        else:
            print(f"Unknown spinner '{args.spinner}'. Options: {', '.join(effects.SPINNERS)}")
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
