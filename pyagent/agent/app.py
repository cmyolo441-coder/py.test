"""Application shell: wires config, provider, tools, memory, commands and UI."""

from __future__ import annotations

import sys

from .commands import build_command_registry
from .config import Config
from .core import Agent
from .memory import Conversation
from .plugins.loader import load_plugins
from .providers import get_provider
from .providers.factory import ProviderError
from .tools import build_default_registry
from .ui import UI
from .logging_config import get_logger

log = get_logger("agent.app")


class App:
    def __init__(self, animations: bool = True) -> None:
        self.config = Config.load()
        self.ui = UI(animations=animations)
        self.registry = build_default_registry()
        self.commands = build_command_registry()
        self.conversation = Conversation(self.config.system_prompt)
        self.agent: Agent | None = None
        self.ui.set_command_source(self._command_items)
        for tool in load_plugins():
            self.registry.register(tool)
            log.info("Loaded plugin tool: %s", tool.name)

    def _command_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for cmd in sorted(self.commands.all(), key=lambda c: c.name):
            desc = (cmd.help or "").strip()
            items.append((cmd.name, desc))
            for alias in getattr(cmd, "aliases", ()):
                items.append((alias, f"alias of {cmd.name}"))
        return items

    def build_agent(self) -> bool:
        if self.config.model:
            owner = self.config.provider_for_model(self.config.model)
            if owner and owner != self.config.provider:
                self.config.provider = owner
        try:
            provider = get_provider(self.config)
        except ProviderError as exc:
            self.ui.error(str(exc))
            self.ui.info("Tip: set the API key env var, or run `/provider ollama` for local mode.")
            return False
        self.agent = Agent(self.config, provider, self.registry, self.conversation)
        log.info("Agent ready: provider=%s model=%s", self.config.provider, self.config.resolved_model())
        return True

    def run(self) -> None:
        self.ui.show_banner(self.config.provider, self.config.resolved_model())
        if not self.config.has_credentials():
            self.ui.warn(f"No credentials found for provider '{self.config.provider}'.")
        self.build_agent()

        while True:
            try:
                user_input = self.ui.prompt().strip()
            except (EOFError, KeyboardInterrupt):
                self.ui.info("Goodbye! \U0001f44b")
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                result = self.commands.dispatch(self, user_input)
                if result.exit_app:
                    break
                continue
            if self.agent is None and not self.build_agent():
                continue
            self._handle_turn(user_input)

    def run_once(self, prompt: str) -> None:
        if not self.build_agent():
            return
        self._handle_turn(prompt)

    def _handle_turn(self, user_input: str) -> None:
        assert self.agent is not None
        from .cancellation import CancellationToken, EscListener

        renderer = self.ui.stream_response()
        renderer.start_thinking()
        cancel_token = CancellationToken()

        def _notify_cancel() -> None:
            renderer.mark_cancelled()

        def on_delta(chunk: str) -> None:
            renderer.on_delta(chunk)

        def on_tool_start(tc) -> bool:
            renderer.finish()
            self.ui.tool_panel(tc.name, tc.arguments)
            tool = self.registry.get(tc.name)
            if tool and tool.dangerous and not self.config.auto_approve_tools:
                return self.ui.confirm(f"Run dangerous tool '{tc.name}'?")
            return True

        def on_tool_result(tc, output: str, success: bool) -> None:
            self.ui.tool_result(tc.name, output, success)

        def on_thinking(iteration: int) -> None:
            if iteration > 0:
                renderer.start_thinking("reasoning")

        try:
            with EscListener(cancel_token, on_cancel=_notify_cancel):
                final = self.agent.send(
                    user_input,
                    on_delta=on_delta if self.config.stream else None,
                    on_tool_start=on_tool_start,
                    on_tool_result=on_tool_result,
                    on_thinking=on_thinking,
                    cancel_token=cancel_token,
                )
        except Exception as exc:
            renderer.finish()
            log.exception("Turn failed")
            self.ui.error(f"{type(exc).__name__}: {exc}")
            return
        renderer.finish(final if not self.config.stream else None)
        if cancel_token.cancelled:
            self.ui.warn("Response stopped (Esc).")
        self.ui.status_bar(
            self.config.provider,
            self.config.resolved_model(),
            self.conversation.token_estimate(),
        )


def main() -> None:
    try:
        from . import __version__
        from .cli import parse_args

        args = parse_args()
        if args.version:
            print(f"terminal-agent {__version__}")
            return

        app = App(animations=not args.no_anim)
        if args.theme:
            from . import themes
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
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
