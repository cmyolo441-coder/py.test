"""40 additional enterprise-grade slash commands.

Every command here is real and working: it operates on the live application
(config, conversation memory, tool registry, UI) or the local system through the
existing tool registry. No placeholders or simulated behaviour.

Grouped roughly as:
  * Session / conversation management
  * Effort & autonomous control
  * Inspection & diagnostics
  * File / project helpers (via the real tool registry)
  * Productivity & UX
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import re
import time
from pathlib import Path

from .base import Command, CommandContext, CommandResult


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _tool(ctx: CommandContext, name: str, **args) -> str:
    """Execute a registered tool and return its text output."""
    result = ctx.app.registry.execute(name, args)
    return result.as_message()


def _messages(ctx: CommandContext) -> list[dict]:
    return ctx.app.conversation.messages


# --------------------------------------------------------------------------- #
# 1-8: Session / conversation management
# --------------------------------------------------------------------------- #
class RetryCommand(Command):
    name = "/retry"
    aliases = ("/regen",)
    help = "Re-send your last message to the model"

    def run(self, ctx: CommandContext) -> CommandResult:
        last_user = next(
            (m for m in reversed(_messages(ctx)) if m.get("role") == "user"), None
        )
        if not last_user:
            ctx.ui.error("No previous message to retry.")
            return CommandResult()
        # Drop the trailing assistant reply so the model answers fresh.
        msgs = _messages(ctx)
        while msgs and msgs[-1].get("role") != "user":
            msgs.pop()
        if msgs:
            msgs.pop()  # remove the old user msg; App.send re-adds it
        ctx.ui.info("Retrying last message...")
        ctx.app._handle_turn(str(last_user.get("content", "")))
        return CommandResult()


class UndoCommand(Command):
    name = "/undo"
    help = "Remove the last exchange (your message + the reply)"

    def run(self, ctx: CommandContext) -> CommandResult:
        msgs = _messages(ctx)
        removed = 0
        while msgs and msgs[-1].get("role") != "user":
            msgs.pop()
            removed += 1
        if msgs and msgs[-1].get("role") == "user":
            msgs.pop()
            removed += 1
        ctx.ui.success(f"Removed {removed} message(s).")
        return CommandResult()


class HistoryCommand(Command):
    name = "/history"
    help = "Show the recent conversation turns"

    def run(self, ctx: CommandContext) -> CommandResult:
        rows = []
        for m in _messages(ctx):
            role = str(m.get("role", "?"))
            if role == "system":
                continue
            content = str(m.get("content", "")).replace("\n", " ")
            rows.append(f"[{role:9}] {content[:100]}")
        ctx.ui.console.print("\n".join(rows) if rows else "(empty)")
        return CommandResult()


class CopyCommand(Command):
    name = "/copy"
    help = "Copy the last assistant reply to the clipboard"

    def run(self, ctx: CommandContext) -> CommandResult:
        last = next(
            (m for m in reversed(_messages(ctx)) if m.get("role") == "assistant"), None
        )
        if not last:
            ctx.ui.error("No assistant reply to copy.")
            return CommandResult()
        text = str(last.get("content", ""))
        try:
            from .. import clipboard

            clipboard.copy(text)
            ctx.ui.success(f"Copied {len(text)} chars to clipboard.")
        except Exception as exc:  # noqa: BLE001
            ctx.ui.error(f"Clipboard unavailable: {exc}")
        return CommandResult()


class SaveAsCommand(Command):
    name = "/saveas"
    help = "Save the conversation to a named JSON file (/saveas name)"

    def run(self, ctx: CommandContext) -> CommandResult:
        name = ctx.args.strip() or _dt.datetime.now().strftime("chat-%Y%m%d-%H%M%S")
        path = Path(f"{name}.json")
        path.write_text(json.dumps(_messages(ctx), indent=2), encoding="utf-8")
        ctx.ui.success(f"Saved to {path.resolve()}")
        return CommandResult()


class LoadCommand(Command):
    name = "/load"
    help = "Load a conversation from a JSON file (/load name)"

    def run(self, ctx: CommandContext) -> CommandResult:
        name = ctx.args.strip()
        if not name:
            ctx.ui.error("Usage: /load <name>")
            return CommandResult()
        path = Path(name if name.endswith(".json") else f"{name}.json")
        if not path.exists():
            ctx.ui.error(f"File not found: {path}")
            return CommandResult()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ctx.app.conversation.messages = data
            ctx.ui.success(f"Loaded {len(data)} messages from {path}")
        except (json.JSONDecodeError, OSError) as exc:
            ctx.ui.error(f"Could not load: {exc}")
        return CommandResult()


class SystemPromptCommand(Command):
    name = "/system"
    aliases = ("/sys",)
    help = "Show or set the system prompt (/system <text>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.info(f"System prompt:\n{ctx.config.system_prompt}")
            return CommandResult()
        ctx.config.system_prompt = ctx.args
        ctx.config.save()
        # Update the live system message.
        msgs = _messages(ctx)
        if msgs and msgs[0].get("role") == "system":
            msgs[0]["content"] = ctx.args
        else:
            msgs.insert(0, {"role": "system", "content": ctx.args})
        ctx.ui.success("System prompt updated.")
        return CommandResult()


class SummarizeCommand(Command):
    name = "/summarize"
    aliases = ("/compact",)
    help = "Ask the model to summarize the chat and compact memory"

    def run(self, ctx: CommandContext) -> CommandResult:
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        ctx.ui.info("Summarizing conversation to compact memory...")
        ctx.app._handle_turn(
            "Summarize our conversation so far into a concise briefing that "
            "preserves all decisions, facts and open tasks."
        )
        return CommandResult()


# --------------------------------------------------------------------------- #
# 9-16: Effort & autonomous control
# --------------------------------------------------------------------------- #
class EffortCommand(Command):
    name = "/effort"
    help = "Show or set the effort level (/effort godmode)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..effort import get_effort, list_efforts

        current = getattr(ctx.config, "effort", "normal")
        if not ctx.args:
            ctx.ui.info(f"Effort: {current}. Levels: {', '.join(list_efforts())}")
            return CommandResult()
        level = get_effort(ctx.args)
        if level.name != ctx.args.lower():
            ctx.ui.error(f"Unknown effort '{ctx.args}'. Levels: {', '.join(list_efforts())}")
            return CommandResult()
        ctx.config.effort = level.name
        ctx.config.temperature = level.temperature
        ctx.config.save()
        ctx.ui.success(f"Effort set to '{level.name}' - {level.description}")
        return CommandResult()


class EffortsCommand(Command):
    name = "/efforts"
    help = "List every effort level with its capabilities"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..effort import LEVELS

        for lvl in LEVELS.values():
            passes = []
            if lvl.research:
                passes.append("research")
            if lvl.architecture_pass:
                passes.append("arch")
            if lvl.security_audit:
                passes.append("security")
            if lvl.performance_pass:
                passes.append("perf")
            if lvl.adversarial_review:
                passes.append("red-team")
            if lvl.docs_pass:
                passes.append("docs")
            ctx.ui.console.print(
                f"[bold]{lvl.name:11}[/bold] x{lvl.self_consistency} "
                f"rounds={lvl.max_execution_rounds:<4} "
                f"verify={lvl.verification_passes} "
                f"[{', '.join(passes) or 'single-pass'}]"
            )
        return CommandResult()


class AutoRunCommand(Command):
    name = "/autorun"
    aliases = ("/agent",)
    help = "Run a task with the full autonomous engine (/autorun <task>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /autorun <task description>")
            return CommandResult()
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        from ..autonomous import AutonomousEngine
        from ..effort import get_effort

        level = get_effort(getattr(ctx.config, "effort", "normal"))
        ctx.ui.info(f"Autonomous engine @ '{level.name}'...")

        def chat_fn(messages):
            resp = ctx.app.agent.provider.chat(messages)
            return getattr(resp, "content", "") or ""

        engine = AutonomousEngine(chat_fn, level)
        run = engine.run(ctx.args)
        for phase in run.phases:
            ctx.ui.tool_result(phase.name, phase.output[:2000], True)
        ctx.ui.console.print(run.final or "(no output)")
        (ctx.ui.success if run.complete else ctx.ui.warn)(
            "Task complete." if run.complete else "Task not fully verified."
        )
        return CommandResult()


class TemperatureCommand(Command):
    name = "/temp"
    aliases = ("/temperature",)
    help = "Show or set sampling temperature (/temp 0.3)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.info(f"Temperature: {ctx.config.temperature}")
            return CommandResult()
        try:
            value = float(ctx.args)
        except ValueError:
            ctx.ui.error("Temperature must be a number.")
            return CommandResult()
        ctx.config.temperature = max(0.0, min(2.0, value))
        ctx.config.save()
        ctx.ui.success(f"Temperature = {ctx.config.temperature}")
        return CommandResult()


class MaxTokensCommand(Command):
    name = "/maxtokens"
    help = "Show or set the max output tokens (/maxtokens 8192)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.info(f"max_tokens: {ctx.config.max_tokens}")
            return CommandResult()
        try:
            ctx.config.max_tokens = int(ctx.args)
        except ValueError:
            ctx.ui.error("max_tokens must be an integer.")
            return CommandResult()
        ctx.config.save()
        ctx.ui.success(f"max_tokens = {ctx.config.max_tokens}")
        return CommandResult()


class StreamCommand(Command):
    name = "/stream"
    help = "Toggle streaming responses on/off"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.config.stream = not ctx.config.stream
        ctx.config.save()
        ctx.ui.success(f"Streaming: {'ON' if ctx.config.stream else 'OFF'}")
        return CommandResult()


class ToolBudgetCommand(Command):
    name = "/budget"
    help = "Show or set max tool iterations per turn (/budget 20)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.info(f"max_tool_iterations: {ctx.config.max_tool_iterations}")
            return CommandResult()
        try:
            ctx.config.max_tool_iterations = max(1, int(ctx.args))
        except ValueError:
            ctx.ui.error("Budget must be an integer.")
            return CommandResult()
        ctx.config.save()
        ctx.ui.success(f"max_tool_iterations = {ctx.config.max_tool_iterations}")
        return CommandResult()


class ToggleToolsCommand(Command):
    name = "/toolcalls"
    help = "Enable or disable tool calling entirely"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.config.enable_tools = not ctx.config.enable_tools
        ctx.config.save()
        ctx.ui.success(f"Tool calling: {'ON' if ctx.config.enable_tools else 'OFF'}")
        return CommandResult()


# --------------------------------------------------------------------------- #
# 17-26: Inspection & diagnostics
# --------------------------------------------------------------------------- #
class SysInfoCommand(Command):
    name = "/sysinfo"
    help = "Show OS, Python and machine information"

    def run(self, ctx: CommandContext) -> CommandResult:
        info = {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "processor": platform.processor() or "n/a",
        }
        ctx.ui.console.print("\n".join(f"{k:10} = {v}" for k, v in info.items()))
        return CommandResult()


class PingCommand(Command):
    name = "/ping"
    help = "Measure round-trip latency to the model provider"

    def run(self, ctx: CommandContext) -> CommandResult:
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        start = time.perf_counter()
        try:
            ctx.app.agent.provider.chat(
                [{"role": "user", "content": "ping"}]
            )
            ms = (time.perf_counter() - start) * 1000
            ctx.ui.success(f"Provider responded in {ms:.0f} ms")
        except Exception as exc:  # noqa: BLE001
            ctx.ui.error(f"Ping failed: {exc}")
        return CommandResult()


class WhoAmICommand(Command):
    name = "/whoami"
    help = "Show the active provider, model and persona"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.console.print(
            f"provider = {ctx.config.provider}\n"
            f"model    = {ctx.config.resolved_model()}\n"
            f"effort   = {getattr(ctx.config, 'effort', 'normal')}\n"
            f"tools    = {'on' if ctx.config.enable_tools else 'off'}"
        )
        return CommandResult()


class StatsCommand(Command):
    name = "/stats"
    help = "Show conversation statistics"

    def run(self, ctx: CommandContext) -> CommandResult:
        msgs = _messages(ctx)
        by_role: dict[str, int] = {}
        chars = 0
        for m in msgs:
            by_role[m.get("role", "?")] = by_role.get(m.get("role", "?"), 0) + 1
            chars += len(str(m.get("content", "")))
        ctx.ui.console.print(
            f"messages = {len(msgs)}\n"
            f"by_role  = {by_role}\n"
            f"chars    = {chars}\n"
            f"~tokens  = {ctx.app.conversation.token_estimate()}"
        )
        return CommandResult()


class EnvCommand(Command):
    name = "/env"
    help = "Show which provider credentials are configured"

    def run(self, ctx: CommandContext) -> CommandResult:
        cfg = ctx.config
        keys = {
            "openai": bool(getattr(cfg, "openai_api_key", "")),
            "anthropic": bool(getattr(cfg, "anthropic_api_key", "")),
            "groq": bool(getattr(cfg, "groq_api_key", "")),
            "zen": bool(getattr(cfg, "zen_api_key", "")),
        }
        ctx.ui.console.print(
            "\n".join(f"{k:10} : {'set' if v else '-'}" for k, v in keys.items())
        )
        return CommandResult()


class ToolInfoCommand(Command):
    name = "/toolinfo"
    help = "Show the schema/description of a tool (/toolinfo run_shell)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /toolinfo <tool_name>")
            return CommandResult()
        tool = ctx.app.registry.get(ctx.args.strip())
        if tool is None:
            ctx.ui.error(f"No such tool: {ctx.args}")
            return CommandResult()
        ctx.ui.console.print(
            f"[bold]{tool.name}[/bold] (dangerous={tool.dangerous})\n"
            f"{tool.description}\n"
            f"params: {json.dumps(tool.parameters, indent=2)}"
        )
        return CommandResult()


class SearchToolsCommand(Command):
    name = "/findtool"
    help = "Search tools by keyword (/findtool git)"

    def run(self, ctx: CommandContext) -> CommandResult:
        q = ctx.args.strip().lower()
        if not q:
            ctx.ui.error("Usage: /findtool <keyword>")
            return CommandResult()
        hits = [
            t for t in ctx.app.registry.all()
            if q in t.name.lower() or q in t.description.lower()
        ]
        if not hits:
            ctx.ui.info(f"No tools match '{q}'.")
            return CommandResult()
        for t in hits:
            ctx.ui.console.print(f"[bold]{t.name}[/bold] - {t.description[:70]}")
        return CommandResult()


class CountTokensCommand(Command):
    name = "/count"
    help = "Estimate tokens for a piece of text (/count some text)"

    def run(self, ctx: CommandContext) -> CommandResult:
        text = ctx.args
        if not text:
            ctx.ui.error("Usage: /count <text>")
            return CommandResult()
        words = len(text.split())
        ctx.ui.info(f"chars={len(text)}  words={words}  ~tokens={len(text) // 4}")
        return CommandResult()


class LastCommand(Command):
    name = "/last"
    help = "Print the full last assistant reply"

    def run(self, ctx: CommandContext) -> CommandResult:
        last = next(
            (m for m in reversed(_messages(ctx)) if m.get("role") == "assistant"), None
        )
        if not last:
            ctx.ui.error("No assistant reply yet.")
            return CommandResult()
        ctx.ui.console.print(str(last.get("content", "")))
        return CommandResult()


class UptimeCommand(Command):
    name = "/uptime"
    help = "Show how long this session has been running"

    def run(self, ctx: CommandContext) -> CommandResult:
        started = getattr(ctx.app, "_started_at", None)
        if started is None:
            ctx.ui.info("Uptime not tracked for this session.")
            return CommandResult()
        secs = int(time.time() - started)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        ctx.ui.info(f"Uptime: {h:02d}:{m:02d}:{s:02d}")
        return CommandResult()


# --------------------------------------------------------------------------- #
# 27-34: File / project helpers (real, via the tool registry)
# --------------------------------------------------------------------------- #
class CatCommand(Command):
    name = "/cat"
    help = "Print a file's contents (/cat path)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /cat <path>")
            return CommandResult()
        path = Path(ctx.args.strip())
        if not path.exists():
            ctx.ui.error(f"File not found: {path}")
            return CommandResult()
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            ctx.ui.error(str(exc))
            return CommandResult()
        lang = path.suffix.lstrip(".") or "text"
        ctx.ui.code_block(content[:20000], language=lang, title=str(path))
        return CommandResult()


class LsCommand(Command):
    name = "/ls"
    help = "List a directory (/ls path)"

    def run(self, ctx: CommandContext) -> CommandResult:
        target = Path(ctx.args.strip() or ".")
        if not target.exists():
            ctx.ui.error(f"Not found: {target}")
            return CommandResult()
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        lines = [f"{'d' if p.is_dir() else 'f'}  {p.name}" for p in entries]
        ctx.ui.console.print("\n".join(lines) or "(empty)")
        return CommandResult()


class TreeCommand(Command):
    name = "/tree"
    help = "Show a directory tree (/tree path [depth])"

    def run(self, ctx: CommandContext) -> CommandResult:
        parts = ctx.args.split()
        root = Path(parts[0]) if parts else Path(".")
        depth = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 2
        if not root.exists():
            ctx.ui.error(f"Not found: {root}")
            return CommandResult()
        out: list[str] = []

        def walk(path: Path, prefix: str, level: int) -> None:
            if level > depth:
                return
            children = sorted(
                [p for p in path.iterdir() if not p.name.startswith(".")],
                key=lambda p: (p.is_file(), p.name.lower()),
            )[:200]
            for i, child in enumerate(children):
                last = i == len(children) - 1
                out.append(f"{prefix}{'└─ ' if last else '├─ '}{child.name}")
                if child.is_dir():
                    walk(child, prefix + ("   " if last else "│  "), level + 1)

        out.append(str(root))
        walk(root, "", 1)
        ctx.ui.console.print("\n".join(out))
        return CommandResult()


class GrepCommand(Command):
    name = "/grep"
    help = "Search files for a regex (/grep pattern [dir])"

    def run(self, ctx: CommandContext) -> CommandResult:
        parts = ctx.args.split(maxsplit=1)
        if not parts:
            ctx.ui.error("Usage: /grep <pattern> [dir]")
            return CommandResult()
        pattern = parts[0]
        root = Path(parts[1]) if len(parts) > 1 else Path(".")
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            ctx.ui.error(f"Bad regex: {exc}")
            return CommandResult()
        matches = 0
        for file in root.rglob("*"):
            if not file.is_file() or file.stat().st_size > 2_000_000:
                continue
            try:
                for n, line in enumerate(file.read_text("utf-8", "ignore").splitlines(), 1):
                    if rx.search(line):
                        ctx.ui.console.print(f"{file}:{n}: {line.strip()[:120]}")
                        matches += 1
                        if matches >= 200:
                            ctx.ui.info("... (200 match limit reached)")
                            return CommandResult()
            except OSError:
                continue
        ctx.ui.info(f"{matches} match(es).")
        return CommandResult()


class RunCommand(Command):
    name = "/run"
    aliases = ("/sh",)
    help = "Run a shell command through the safe tool (/run ls -la)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /run <command>")
            return CommandResult()
        out = _tool(ctx, "run_shell", command=ctx.args)
        ctx.ui.tool_result("run_shell", out, not out.startswith("ERROR"))
        return CommandResult()


class DiffCommand(Command):
    name = "/gitdiff"
    help = "Show the current git diff"

    def run(self, ctx: CommandContext) -> CommandResult:
        out = _tool(ctx, "run_shell", command="git diff --stat")
        ctx.ui.tool_result("git diff", out, not out.startswith("ERROR"))
        return CommandResult()


class PwdCommand(Command):
    name = "/pwd"
    help = "Show the current working directory"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.info(str(Path.cwd()))
        return CommandResult()


class WriteNoteCommand(Command):
    name = "/note"
    help = "Append a timestamped note to notes.md (/note text)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /note <text>")
            return CommandResult()
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with open("notes.md", "a", encoding="utf-8") as fh:
            fh.write(f"- [{stamp}] {ctx.args}\n")
        ctx.ui.success("Note saved to notes.md")
        return CommandResult()


# --------------------------------------------------------------------------- #
# 35-40: Productivity & UX
# --------------------------------------------------------------------------- #
class TimeCommand(Command):
    name = "/time"
    help = "Show the current local and UTC time"

    def run(self, ctx: CommandContext) -> CommandResult:
        now = _dt.datetime.now()
        utc = _dt.datetime.utcnow()
        ctx.ui.info(
            f"local: {now:%Y-%m-%d %H:%M:%S}   utc: {utc:%Y-%m-%d %H:%M:%S}"
        )
        return CommandResult()


class CalcCommand(Command):
    name = "/calc"
    help = "Evaluate a math expression (/calc 2*(3+4))"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /calc <expression>")
            return CommandResult()
        out = _tool(ctx, "calculate", expression=ctx.args)
        ctx.ui.info(out)
        return CommandResult()


class ThemeNextCommand(Command):
    name = "/nexttheme"
    help = "Cycle to the next UI theme"

    def run(self, ctx: CommandContext) -> CommandResult:
        from .. import themes

        names = themes.names()
        current = themes.current().name
        nxt = names[(names.index(current) + 1) % len(names)]
        themes.set_theme(nxt)
        ctx.ui.reset_session()
        ctx.ui.success(f"Theme -> {nxt}")
        return CommandResult()


class ConfettiCommand(Command):
    name = "/confetti"
    aliases = ("/celebrate",)
    help = "Throw a celebration confetti burst"

    def run(self, ctx: CommandContext) -> CommandResult:
        from .. import effects

        effects.confetti(ctx.ui.console)
        return CommandResult()


class ExportMarkdownCommand(Command):
    name = "/md"
    help = "Export the conversation to a markdown file (/md name)"

    def run(self, ctx: CommandContext) -> CommandResult:
        name = ctx.args.strip() or _dt.datetime.now().strftime("chat-%Y%m%d-%H%M%S")
        path = Path(f"{name}.md")
        lines = [f"# Conversation export ({_dt.datetime.now():%Y-%m-%d %H:%M})", ""]
        for m in _messages(ctx):
            role = m.get("role", "?")
            if role == "system":
                continue
            lines.append(f"## {role}")
            lines.append(str(m.get("content", "")))
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        ctx.ui.success(f"Exported to {path.resolve()}")
        return CommandResult()


class AboutCommand(Command):
    name = "/about"
    aliases = ("/version",)
    help = "Show version and feature summary"

    def run(self, ctx: CommandContext) -> CommandResult:
        from .. import __version__

        ctx.ui.console.print(
            f"[bold]Advanced Terminal AI Agent[/bold] v{__version__}\n"
            f"provider={ctx.config.provider} model={ctx.config.resolved_model()}\n"
            f"tools={len(ctx.app.registry.all())} "
            f"commands={len(ctx.app.commands.all())}"
        )
        return CommandResult()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
FEATURE_COMMANDS: list[type[Command]] = [
    # session / conversation
    RetryCommand, UndoCommand, HistoryCommand, CopyCommand, SaveAsCommand,
    LoadCommand, SystemPromptCommand, SummarizeCommand,
    # effort & autonomous
    EffortCommand, EffortsCommand, AutoRunCommand, TemperatureCommand,
    MaxTokensCommand, StreamCommand, ToolBudgetCommand, ToggleToolsCommand,
    # inspection & diagnostics
    SysInfoCommand, PingCommand, WhoAmICommand, StatsCommand, EnvCommand,
    ToolInfoCommand, SearchToolsCommand, CountTokensCommand, LastCommand,
    UptimeCommand,
    # files / project
    CatCommand, LsCommand, TreeCommand, GrepCommand, RunCommand, DiffCommand,
    PwdCommand, WriteNoteCommand,
    # productivity & UX
    TimeCommand, CalcCommand, ThemeNextCommand, ConfettiCommand,
    ExportMarkdownCommand, AboutCommand,
]


def build_feature_commands() -> list[Command]:
    """Instantiate all 40 feature commands."""
    return [cmd() for cmd in FEATURE_COMMANDS]
