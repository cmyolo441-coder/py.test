from agent.commands import Command, CommandRegistry
from agent.prompt_ui import SLASH_COMMANDS
from agent.themes import current, names, set_theme


def test_slash_commands_present():
    assert "/help" in SLASH_COMMANDS
    assert "/exit" in SLASH_COMMANDS
    assert "/matrix" in SLASH_COMMANDS


def test_themes():
    assert "neon" in names()
    assert set_theme("cyberpunk")
    assert current().name == "cyberpunk"
    assert not set_theme("nonexistent")


def test_command_registry():
    class HiCommand(Command):
        name = "/hi"
        aliases = ("/hello",)
        help = "say hi"

        def run(self, ctx):
            return "hello"

    reg = CommandRegistry()
    reg.register(HiCommand())
    # Resolution works by name and by alias.
    assert reg.resolve("/hi").name == "/hi"
    assert reg.resolve("/hello").name == "/hi"
    # Unknown tokens resolve to None.
    assert reg.resolve("/nope") is None
