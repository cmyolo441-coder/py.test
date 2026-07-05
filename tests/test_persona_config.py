"""Tests for the persona and config commands via a lightweight fake app."""

from __future__ import annotations

from agent.commands.persona_command import PersonaCommand
from agent.commands.config_command import ConfigCommand
from agent.commands.base import CommandContext
from agent.config import Config
from agent.memory import Conversation


class _FakeUI:
    def __init__(self):
        self.messages = []

    class _Console:
        def print(self, *a, **k):
            pass

    console = _Console()

    def info(self, t): self.messages.append(("info", t))
    def success(self, t): self.messages.append(("success", t))
    def error(self, t): self.messages.append(("error", t))


class _FakeApp:
    def __init__(self):
        self.config = Config()
        self.ui = _FakeUI()
        self.conversation = Conversation(self.config.system_prompt)

    def build_agent(self):
        return True


def _ctx(app, args):
    return CommandContext(app=app, raw="", args=args)


def test_persona_switch():
    app = _FakeApp()
    result = PersonaCommand().run(_ctx(app, "coder"))
    assert result.handled
    assert "senior software engineer" in app.config.system_prompt


def test_persona_unknown():
    app = _FakeApp()
    PersonaCommand().run(_ctx(app, "nope"))
    assert any(kind == "error" for kind, _ in app.ui.messages)


def test_config_set_temperature():
    app = _FakeApp()
    ConfigCommand().run(_ctx(app, "temperature 0.3"))
    assert app.config.temperature == 0.3


def test_config_set_bool():
    app = _FakeApp()
    ConfigCommand().run(_ctx(app, "auto_approve_tools true"))
    assert app.config.auto_approve_tools is True


def test_config_unknown_key():
    app = _FakeApp()
    ConfigCommand().run(_ctx(app, "nope 1"))
    assert any(kind == "error" for kind, _ in app.ui.messages)
