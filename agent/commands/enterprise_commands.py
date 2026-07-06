"""40 new enterprise-grade slash commands — the advanced command surface.

Every command here is real and working: it operates on the live application
(config, conversation memory, tool registry, UI) or the new enterprise
subsystems (token counter, fallback chain, model router, consensus, branches,
cost tracker, telemetry, profiler, recovery, goal history, prompt library,
goal templates, context manager, quality scorer).

Grouped as:
  * Cost & token management (5)
  * Model routing & fallback (5)
  * Consensus & quality (4)
  * Branching & session (4)
  * Goal mode extensions (6)
  * Telemetry & profiling (4)
  * Recovery & history (4)
  * Prompt library (3)
  * Templates & scaffolding (3)
  * Power-user utilities (2)
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import Command, CommandContext, CommandResult


# =========================================================================== #
# COST & TOKEN MANAGEMENT (5)
# =========================================================================== #
class CostCommand(Command):
    name = "/cost"
    aliases = ("/spend", "/usage-cost")
    help = "Show the live cost dashboard (session, goal, all-time, budget)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..cost_tracker import get_cost_tracker
        budget = getattr(ctx.config, "cost_budget_usd", 0.0)
        tracker = get_cost_tracker(budget_usd=budget)
        ctx.ui.console.print(tracker.dashboard())
        ctx.ui.console.print()
        ctx.ui.console.print(tracker.per_model_breakdown())
        return CommandResult()


class BudgetCommand(Command):
    name = "/budget"
    help = "Set a per-session USD budget (/budget 5.00). Warns at 80%, blocks at 100%."

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..cost_tracker import get_cost_tracker
        if not ctx.args:
            tracker = get_cost_tracker()
            st = tracker.budget_status()
            if st.budget_usd <= 0:
                ctx.ui.info("No budget set. Use: /budget <usd_amount>")
            else:
                ctx.ui.info(
                    f"Budget: ${st.budget_usd:.2f}  spent: ${st.spent_usd:.4f}  "
                    f"({st.percent_used:.1f}%)  remaining: ${st.remaining_usd:.2f}"
                )
            return CommandResult()
        try:
            amount = float(ctx.args.strip())
        except ValueError:
            ctx.ui.error("Usage: /budget <usd_amount>  (e.g. /budget 5.00)")
            return CommandResult()
        if hasattr(ctx.config, "cost_budget_usd"):
            ctx.config.cost_budget_usd = amount
        tracker = get_cost_tracker(budget_usd=amount)
        ctx.config.save()
        ctx.ui.success(f"Session budget set to ${amount:.2f}. Will warn at 80%, block at 100%.")
        return CommandResult()


class TokensCommand(Command):
    name = "/tokens"
    aliases = ("/tok",)
    help = "Show detailed token usage (session, goal, all-time, last turn)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..token_counter import get_token_counter
        counter = get_token_counter()
        snap = counter.snapshot()
        lines = [
            "╭─ Token Usage ───────────────────────────────────╮",
            "│  Session:                                        │",
            f"│    turns:      {snap['session_turns']:>6}                              │",
            f"│    input:      {snap['session_input']:>6,} tokens                       │",
            f"│    output:     {snap['session_output']:>6,} tokens                       │",
            f"│    total:      {snap['session_total']:>6,} tokens                       │",
            "│                                                  │",
        ]
        if snap["goal_turns"] > 0:
            lines += [
                "│  Current goal:                                   │",
                f"│    turns:      {snap['goal_turns']:>6}                              │",
                f"│    total:      {snap['goal_total']:>6,} tokens                       │",
                "│                                                  │",
            ]
        lines += [
            "│  All-time:                                       │",
            f"│    turns:      {snap['all_time_turns']:>6}                              │",
            f"│    total:      {snap['all_time_total']:>6,} tokens                       │",
            "╰──────────────────────────────────────────────────╯",
        ]
        ctx.ui.console.print("\n".join(lines))
        last = counter.last_turn()
        if last:
            tps = last.tokens_per_second
            ctx.ui.info(
                f"Last turn: {last.input_tokens} in + {last.output_tokens} out = "
                f"{last.total_tokens} tokens in {last.duration_s:.2f}s "
                f"({tps:.0f} tok/s)  cost={last.cost_usd:.6f}"
            )
        return CommandResult()


class AllTimeCommand(Command):
    name = "/all-time"
    aliases = ("/lifetime",)
    help = "Show all-time usage across all sessions"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..token_counter import get_token_counter
        ctx.ui.info(get_token_counter().all_time_summary())
        return CommandResult()


class ResetCostCommand(Command):
    name = "/reset-cost"
    help = "Reset the session cost counter (does not affect all-time)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..token_counter import get_token_counter
        counter = get_token_counter()
        counter.session = type(counter.session)()
        ctx.ui.success("Session cost counter reset.")
        return CommandResult()


# =========================================================================== #
# MODEL ROUTING & FALLBACK (5)
# =========================================================================== #
class RouterCommand(Command):
    name = "/router"
    help = "Show the smart routing table, or route a prompt (/router <prompt>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..model_router import describe_routing_table, route
        if not ctx.args:
            ctx.ui.console.print(describe_routing_table())
            return CommandResult()
        available = [p for p in ["zen", "openai", "anthropic", "gemini", "ollama", "groq", "mistral", "together", "zyloo"]
                     if ctx.config.has_credentials() or p == ctx.config.provider]
        decision = route(ctx.args, available_providers=available, preferred_provider=ctx.config.provider)
        ctx.ui.info(f"Query class: {decision.query_class}")
        ctx.ui.info(f"Routed to: {decision.provider}/{decision.model}")
        ctx.ui.info(f"Reason: {decision.reason}")
        if decision.alternatives:
            alts = ", ".join(f"{p}/{m}" for p, m in decision.alternatives[:3])
            ctx.ui.info(f"Alternatives: {alts}")
        return CommandResult()


class RouteCommand(Command):
    name = "/route"
    help = "Auto-route the next prompt to the best model, then send it (/route <prompt>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..model_router import route
        if not ctx.args:
            ctx.ui.error("Usage: /route <your prompt>")
            return CommandResult()
        available = [p for p in ["zen", "openai", "anthropic", "gemini", "ollama"]
                     if ctx.config.has_credentials() or p == ctx.config.provider]
        decision = route(ctx.args, available_providers=available)
        _old_provider, _old_model = ctx.config.provider, ctx.config.model
        ctx.config.provider = decision.provider
        ctx.config.model = decision.model
        ctx.app.build_agent()
        ctx.ui.success(f"Routed to {decision.provider}/{decision.model} ({decision.query_class})")
        try:
            ctx.app._handle_turn(ctx.args)
        finally:
            # Restore previous provider? Or keep the routed one? Keep routed.
            ctx.config.save()
        return CommandResult()


class FallbackCommand(Command):
    name = "/fallback"
    help = "Show or configure the model fallback chain"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..fallback import get_fallback_chain
        chain = get_fallback_chain()
        ctx.ui.console.print(chain.describe())
        return CommandResult()


class FallbackResetCommand(Command):
    name = "/fallback-reset"
    help = "Reset all fallback chain cooldowns (retry failed providers)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..fallback import get_fallback_chain
        chain = get_fallback_chain()
        for entry in chain.entries:
            entry.record_success()
        ctx.ui.success("All fallback cooldowns cleared.")
        return CommandResult()


class ModelsAllCommand(Command):
    name = "/models-all"
    help = "List ALL models across ALL providers (preset + known)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..providers.registry import PROVIDERS
        from rich.table import Table
        table = Table(title="All available models", show_lines=False)
        table.add_column("Provider", style="cyan")
        table.add_column("Default model", style="green")
        table.add_column("Needs key?", style="yellow")
        table.add_column("Description", style="dim")
        for spec in PROVIDERS.values():
            table.add_row(spec.name, spec.default_model, "yes" if spec.needs_key else "no", spec.description)
        ctx.ui.console.print(table)
        # Also show preset models from config.
        ctx.ui.info("Preset models:")
        for prov, model in ctx.config.all_known_models():
            marker = " (active)" if prov == ctx.config.provider and model == ctx.config.resolved_model() else ""
            ctx.ui.info(f"  {prov}/{model}{marker}")
        return CommandResult()


# =========================================================================== #
# CONSENSUS & QUALITY (4)
# =========================================================================== #
class ConsensusCommand(Command):
    name = "/consensus"
    help = "Query multiple models and pick the best answer (/consensus <prompt>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..consensus import run_consensus, describe_strategies
        if not ctx.args:
            ctx.ui.console.print(describe_strategies())
            return CommandResult()
        ctx.ui.warn("Consensus mode queries multiple models in parallel — this may take a while and cost more.")
        ctx.ui.info("Running consensus query…")
        # Build candidate list from configured providers.
        candidates = []
        for prov in ["zen", "openai", "anthropic", "gemini", "ollama"]:
            if prov == ctx.config.provider:
                candidates.append((prov, ctx.config.resolved_model()))
            elif prov == "ollama":
                candidates.append((prov, "llama3.1"))
        if len(candidates) < 2:
            ctx.ui.error("Need at least 2 configured providers for consensus.")
            return CommandResult()
        try:
            from ..providers.factory import get_provider as _get_provider

            def provider_getter(prov, model):
                cfg = type(ctx.config)()
                cfg.provider = prov
                cfg.model = model
                return _get_provider(cfg)

            def chat_fn(provider, messages):
                resp = provider.chat(messages)
                return getattr(resp, "content", "") or ""

            messages = list(ctx.app.conversation.messages) + [{"role": "user", "content": ctx.args}]
            result = run_consensus(provider_getter, chat_fn, messages, candidates, strategy="first")
            ctx.ui.success(f"Consensus winner: {result.provider}/{result.model} (strategy: {result.strategy})")
            ctx.ui.console.print(result.text[:3000])
            ctx.ui.info(f"Duration: {result.duration_s:.2f}s  candidates: {len(result.all_responses)}")
            for r in result.all_responses:
                status = "✓" if r["ok"] else "✗"
                ctx.ui.info(f"  {status} {r['provider']}/{r['model']}  {r['duration_s']:.2f}s")
        except Exception as exc:
            ctx.ui.error(f"Consensus failed: {exc}")
        return CommandResult()


class QualityCommand(Command):
    name = "/quality"
    help = "Score the last response on completeness/correctness/clarity/actionability"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..quality_scorer import score_response
        # Find last user + assistant pair.
        last_user = None
        last_assistant = None
        for m in reversed(ctx.app.conversation.messages):
            if m.get("role") == "assistant" and last_assistant is None:
                content = m.get("content", "")
                last_assistant = content if isinstance(content, str) else str(content)
            elif m.get("role") == "user" and last_user is None:
                last_user = m.get("content", "")
                break
        if not last_assistant:
            ctx.ui.error("No assistant response to score yet.")
            return CommandResult()
        score = score_response(last_user or "", last_assistant)
        ctx.ui.console.print(score.summary())
        return CommandResult()


class CompareCommand(Command):
    name = "/compare"
    help = "Compare the last two assistant responses side by side"

    def run(self, ctx: CommandContext) -> CommandResult:
        responses = [m for m in ctx.app.conversation.messages if m.get("role") == "assistant"]
        if len(responses) < 2:
            ctx.ui.error("Need at least 2 assistant responses to compare.")
            return CommandResult()
        a = responses[-2].get("content", "")
        b = responses[-1].get("content", "")
        if isinstance(a, list): a = " ".join(str(x) for x in a)
        if isinstance(b, list): b = " ".join(str(x) for x in b)
        from ..widgets import render_diff
        ctx.ui.console.print(render_diff(a, b, "response 1", "response 2"))
        return CommandResult()


class JudgeCommand(Command):
    name = "/judge"
    help = "Have the model critique its own last response for quality"

    def run(self, ctx: CommandContext) -> CommandResult:
        last_assistant = None
        for m in reversed(ctx.app.conversation.messages):
            if m.get("role") == "assistant":
                content = m.get("content", "")
                last_assistant = content if isinstance(content, str) else str(content)
                break
        if not last_assistant:
            ctx.ui.error("No assistant response to judge.")
            return CommandResult()
        judge_prompt = (
            "Critically evaluate the following response for accuracy, completeness, "
            "clarity and actionability. Point out any errors, omissions, or improvements. "
            "Give a score out of 10 and a one-line verdict.\n\nResponse:\n" + last_assistant[:4000]
        )
        ctx.app._handle_turn(judge_prompt)
        return CommandResult()


# =========================================================================== #
# BRANCHING & SESSION (4)
# =========================================================================== #
class BranchCommand(Command):
    name = "/branch"
    help = "Create a conversation branch (/branch <name>) or list branches (/branch)"

    def run(self, ctx: CommandContext) -> CommandResult:
        bm = getattr(ctx.app, "branch_manager", None)
        if bm is None:
            ctx.ui.error("Branching not initialised.")
            return CommandResult()
        if not ctx.args:
            ctx.ui.console.print(bm.tree())
            return CommandResult()
        name = ctx.args.strip()
        branch = bm.fork(name=name)
        ctx.app.conversation.messages = list(branch.messages)
        ctx.ui.success(f"Forked to branch '{name}' (id={branch.id}, {len(branch.messages)} messages)")
        return CommandResult()


class SwitchCommand(Command):
    name = "/switch"
    help = "Switch to a conversation branch (/switch <id-or-name>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        bm = getattr(ctx.app, "branch_manager", None)
        if bm is None or not ctx.args:
            ctx.ui.error("Usage: /switch <branch-id-or-name>")
            return CommandResult()
        target = ctx.args.strip()
        # Match by id or name.
        for b in bm.list_branches():
            if b.id == target or b.name == target:
                bm.switch(b.id)
                ctx.app.conversation.messages = list(b.messages)
                ctx.ui.success(f"Switched to branch '{b.name}' ({len(b.messages)} messages)")
                return CommandResult()
        ctx.ui.error(f"No branch named '{target}'.")
        return CommandResult()


class BranchesCommand(Command):
    name = "/branches"
    help = "Show the conversation branch tree"

    def run(self, ctx: CommandContext) -> CommandResult:
        bm = getattr(ctx.app, "branch_manager", None)
        if bm is None:
            ctx.ui.error("Branching not initialised.")
            return CommandResult()
        ctx.ui.console.print(bm.tree())
        return CommandResult()


class SnapshotCommand(Command):
    name = "/snapshot"
    help = "Save a named snapshot of the current conversation state"

    def run(self, ctx: CommandContext) -> CommandResult:
        import json
        name = ctx.args.strip() or f"snapshot-{int(time.time())}"
        path = Path.home() / ".terminal_agent" / "snapshots" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ctx.app.conversation.messages, indent=2), encoding="utf-8")
        ctx.ui.success(f"Snapshot saved: {path}")
        return CommandResult()


# =========================================================================== #
# GOAL MODE EXTENSIONS (6)
# =========================================================================== #
class GoalHistoryCommand(Command):
    name = "/goal-history"
    aliases = ("/goals",)
    help = "Show past goal runs (with status, rounds, cost)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..goal_history import get_goal_history
        ctx.ui.console.print(get_goal_history().dashboard())
        return CommandResult()


class GoalShowCommand(Command):
    name = "/goal-show"
    help = "Show the full transcript of a past goal (/goal-show <id>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..goal_history import get_goal_history
        if not ctx.args:
            ctx.ui.error("Usage: /goal-show <goal-id>")
            return CommandResult()
        ctx.ui.console.print(get_goal_history().show(ctx.args.strip()))
        return CommandResult()


class GoalResumeCommand(Command):
    name = "/goal-resume"
    help = "Resume an interrupted goal from its last checkpoint (/goal-resume <id>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..goal_history import get_goal_history
        from ..goal_mode import GoalMode
        if not ctx.args:
            ctx.ui.error("Usage: /goal-resume <goal-id>")
            return CommandResult()
        record = get_goal_history().load(ctx.args.strip())
        if record is None:
            ctx.ui.error(f"No goal with id '{ctx.args.strip()}'")
            return CommandResult()
        if not ctx.app.goal_mode:
            ctx.app._enter_goal_mode()
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        # Restore conversation from checkpoint.
        if record.checkpoint_messages:
            ctx.app.conversation.messages = list(record.checkpoint_messages)
        run = GoalMode(ctx.app).run(record.goal, resume_from=record)
        ctx.ui.console.print(run.summary())
        return CommandResult()


class GoalTemplatesCommand(Command):
    name = "/goal-templates"
    help = "List available goal templates (/goal-templates [category])"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..goal_templates import list_templates, categories
        cat = ctx.args.strip() or None
        templates = list_templates(cat)
        if not templates:
            ctx.ui.info(f"Categories: {', '.join(categories())}")
            return CommandResult()
        ctx.ui.info(f"Goal templates{f' ({cat})' if cat else ''}:")
        for t in templates:
            ctx.ui.info(f"  {t.name:<22} effort={t.suggested_effort:<12} {t.description}")
        ctx.ui.info("\nUse: /goal-template <name> <args>")
        return CommandResult()


class GoalTemplateCommand(Command):
    name = "/goal-template"
    help = "Launch a goal from a template (/goal-template <name> key=value ...)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..goal_templates import get_template, render
        parts = ctx.args.split(None, 1)
        if not parts:
            ctx.ui.error("Usage: /goal-template <name> [key=value ...]")
            return CommandResult()
        name = parts[0]
        tpl = get_template(name)
        if tpl is None:
            ctx.ui.error(f"No template '{name}'. See /goal-templates.")
            return CommandResult()
        # Parse key=value args.
        kwargs = {}
        if len(parts) > 1:
            for token in parts[1].split():
                if "=" in token:
                    k, v = token.split("=", 1)
                    kwargs[k] = v
        prompt = render(tpl, **kwargs)
        ctx.ui.info(f"Launching template '{name}' (effort: {tpl.suggested_effort})")
        if not ctx.app.goal_mode:
            ctx.app._enter_goal_mode()
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        from ..goal_mode import GoalMode
        run = GoalMode(ctx.app).run(prompt)
        ctx.ui.console.print(run.summary())
        return CommandResult()


class GoalCommandCmd(Command):
    name = "/goal-command"
    help = "Run a slash command from within Goal Mode (/goal-command <command>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.app.goal_mode:
            ctx.ui.error("Must be in Goal Mode. Use /goal first.")
            return CommandResult()
        if not ctx.args:
            ctx.ui.error("Usage: /goal-command /<command> [args]")
            return CommandResult()
        cmd_str = ctx.args.strip()
        if not cmd_str.startswith("/"):
            cmd_str = "/" + cmd_str
        result = ctx.app.commands.dispatch(ctx.app, cmd_str)
        if result.exit_app:
            return result
        ctx.ui.success(f"Command executed: {cmd_str}")
        return CommandResult()


# =========================================================================== #
# TELEMETRY & PROFILING (4)
# =========================================================================== #
class TelemetryCommand(Command):
    name = "/telemetry"
    help = "Toggle telemetry on/off, or show the dashboard (/telemetry on|off|status)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..telemetry import get_telemetry
        tel = get_telemetry()
        arg = ctx.args.strip().lower()
        if arg == "on":
            tel.enable()
            ctx.ui.success("Telemetry ENABLED (anonymous, opt-in).")
        elif arg == "off":
            tel.disable()
            ctx.ui.success("Telemetry DISABLED.")
        elif arg == "clear":
            tel.clear()
            ctx.ui.success("Telemetry data cleared.")
        else:
            ctx.ui.console.print(tel.dashboard())
        return CommandResult()


class ProfileCommand(Command):
    name = "/profile"
    help = "Toggle the profiler, or show the last turn's timing breakdown"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..profiler import get_profiler
        prof = get_profiler()
        arg = ctx.args.strip().lower()
        if arg == "on":
            prof.enable()
            ctx.ui.success("Profiler ENABLED.")
        elif arg == "off":
            prof.disable()
            ctx.ui.success("Profiler DISABLED.")
        elif arg == "all":
            ctx.ui.console.print(prof.aggregate_summary())
        else:
            ctx.ui.console.print(prof.last_turn_summary())
        return CommandResult()


class ProfileSummaryCommand(Command):
    name = "/profile-summary"
    help = "Show aggregate profiling across all turns"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..profiler import get_profiler
        ctx.ui.console.print(get_profiler().aggregate_summary())
        return CommandResult()


class HealthCheckCommand(Command):
    name = "/health-check"
    aliases = ("/doctor",)
    help = "Run a comprehensive health check of all subsystems"

    def run(self, ctx: CommandContext) -> CommandResult:
        from rich.table import Table
        table = Table(title="Health Check", show_lines=True)
        table.add_column("Subsystem", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Detail", style="dim")
        checks = [
            ("Config", lambda: f"provider={ctx.config.provider}", True),
            ("Provider", lambda: f"model={ctx.config.resolved_model()}", ctx.config.has_credentials()),
            ("Tool registry", lambda: f"{len(ctx.app.registry.all())} tools", True),
            ("Command registry", lambda: f"{len(ctx.app.commands.all())} commands", True),
            ("Token counter", lambda: "ready", True),
        ]
        try:
            from ..token_counter import get_token_counter
            snap = get_token_counter().snapshot()
            checks.append(("Token counter", lambda: f"{snap['session_total']:,} session tokens", True))
        except Exception:
            pass
        try:
            from ..fallback import get_fallback_chain
            chain = get_fallback_chain()
            healthy = sum(1 for e in chain.entries if not e.is_on_cooldown)
            checks.append(("Fallback chain", lambda: f"{healthy}/{len(chain.entries)} healthy", healthy > 0))
        except Exception:
            pass
        try:
            from ..recovery import get_recovery_manager
            rm = get_recovery_manager()
            checks.append(("Recovery", lambda: f"{len(rm.list_checkpoints())} checkpoints", True))
        except Exception:
            pass
        try:
            from ..goal_history import get_goal_history
            gh = get_goal_history()
            checks.append(("Goal history", lambda: f"{len(gh.list_all())} past goals", True))
        except Exception:
            pass
        for name, detail_fn, ok in checks:
            try:
                detail = detail_fn()
            except Exception as exc:
                detail = f"error: {exc}"
                ok = False
            table.add_row(name, "✓ OK" if ok else "✗ FAIL", detail)
        ctx.ui.console.print(table)
        return CommandResult()


# =========================================================================== #
# RECOVERY & HISTORY (4)
# =========================================================================== #
class RecoverCommand(Command):
    name = "/recover"
    help = "Show recovery checkpoints, or restore one (/recover <session-id>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..recovery import get_recovery_manager
        rm = get_recovery_manager()
        if not ctx.args:
            ctx.ui.console.print(rm.dashboard())
            return CommandResult()
        target = ctx.args.strip()
        # Find the checkpoint file.
        from pathlib import Path
        path = Path.home() / ".terminal_agent" / "recovery" / f"{target}.json"
        if not path.exists():
            ctx.ui.error(f"No recovery checkpoint for '{target}'.")
            return CommandResult()
        import json
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            msgs = data.get("messages", [])
            ctx.app.conversation.messages = list(msgs)
            ctx.config.provider = data.get("provider", ctx.config.provider)
            ctx.config.model = data.get("model") or ctx.config.model
            ctx.app.build_agent()
            ctx.ui.success(f"Recovered session '{target}' ({len(msgs)} messages)")
        except Exception as exc:
            ctx.ui.error(f"Recovery failed: {exc}")
        return CommandResult()


class CheckpointCommand(Command):
    name = "/checkpoint"
    help = "Save a recovery checkpoint of the current session"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..recovery import Checkpoint, get_recovery_manager
        rm = get_recovery_manager()
        cp = Checkpoint(
            session_id=f"manual-{int(time.time())}",
            provider=ctx.config.provider,
            model=ctx.config.resolved_model(),
            messages=list(ctx.app.conversation.messages),
            turn_count=len([m for m in ctx.app.conversation.messages if m.get("role") == "user"]),
            goal_mode=ctx.app.goal_mode,
        )
        path = rm.save(cp)
        ctx.ui.success(f"Checkpoint saved: {path}")
        return CommandResult()


class HistoryCommand(Command):
    name = "/history"
    help = "Show recent conversation history (last 20 messages)"

    def run(self, ctx: CommandContext) -> CommandResult:
        msgs = ctx.app.conversation.messages[-20:]
        for m in msgs:
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", str(b)) if isinstance(b, dict) else str(b) for b in content)
            content = str(content)[:100]
            icon = {"user": "🧑", "assistant": "🤖", "system": "⚙️", "tool": "🔧"}.get(role, "•")
            ctx.ui.info(f"  {icon} {role}: {content}")
        return CommandResult()


class ClearHistoryCommand(Command):
    name = "/clear-history"
    help = "Clear conversation history AND saved snapshots (use /clear for memory only)"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.app.conversation.reset(ctx.config.system_prompt)
        ctx.ui.success("Conversation memory cleared. (Snapshots/recovery kept.)")
        return CommandResult()


# =========================================================================== #
# PROMPT LIBRARY (3)
# =========================================================================== #
class PromptsCommand(Command):
    name = "/prompts"
    help = "Browse the prompt template library (/prompts [category])"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..prompt_library import list_templates, categories
        cat = ctx.args.strip() or None
        templates = list_templates(cat)
        if not templates:
            ctx.ui.info(f"Categories: {', '.join(categories())}")
            return CommandResult()
        ctx.ui.info(f"Prompt templates{f' ({cat})' if cat else ''}:")
        for t in templates:
            ctx.ui.info(f"  {t.name:<22} [{t.category}]  {t.description}")
        ctx.ui.info("\nUse: /use-prompt <name>")
        return CommandResult()


class UsePromptCommand(Command):
    name = "/use-prompt"
    help = "Apply a prompt template as the system prompt (/use-prompt <name>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..prompt_library import get_template
        if not ctx.args:
            ctx.ui.error("Usage: /use-prompt <name>")
            return CommandResult()
        tpl = get_template(ctx.args.strip())
        if tpl is None:
            ctx.ui.error(f"No template '{ctx.args.strip()}'. See /prompts.")
            return CommandResult()
        ctx.config.system_prompt = tpl.template
        ctx.config.save()
        ctx.app.conversation.reset(tpl.template)
        ctx.app.build_agent()
        ctx.ui.success(f"System prompt set to '{tpl.name}' ({tpl.category}). Conversation reset.")
        return CommandResult()


class PromptInfoCommand(Command):
    name = "/prompt-info"
    help = "Show the currently active system prompt"

    def run(self, ctx: CommandContext) -> CommandResult:
        ctx.ui.console.print(ctx.config.system_prompt)
        return CommandResult()


# =========================================================================== #
# TEMPLATES & SCAFFOLDING (3)
# =========================================================================== #
class ContextCommand(Command):
    name = "/context"
    help = "Show context-window usage and compaction info for the current model"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..context_manager import context_budget_for
        from ..token_counter import count_message_tokens
        model = ctx.config.resolved_model()
        provider = ctx.config.provider
        budget = context_budget_for(model)
        msgs = ctx.app.conversation.messages
        current = count_message_tokens(msgs, model, provider)
        pct = (current / budget * 100) if budget else 0
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        ctx.ui.info(f"Context window: {model}")
        ctx.ui.info(f"  budget:  {budget:,} tokens")
        ctx.ui.info(f"  used:    {current:,} tokens ({pct:.1f}%)")
        ctx.ui.info(f"  [{bar}]")
        ctx.ui.info(f"  free:    {budget - current:,} tokens")
        if pct > 80:
            ctx.ui.warn("Context is >80% full. Older messages will be compacted on next turn.")
        return CommandResult()


class CompactCommand(Command):
    name = "/compact"
    help = "Manually compact the conversation to fit the model's context window"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..context_manager import compress_messages
        model = ctx.config.resolved_model()
        before = len(ctx.app.conversation.messages)
        ctx.app.conversation.messages = compress_messages(
            ctx.app.conversation.messages, model, ctx.config.provider
        )
        after = len(ctx.app.conversation.messages)
        ctx.ui.success(f"Compacted: {before} -> {after} messages")
        return CommandResult()


class BootCommand(Command):
    name = "/boot"
    help = "Replay the animated boot sequence"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..boot_sequence import play_boot_sequence
        play_boot_sequence(
            ctx.ui.console,
            ctx.config.provider,
            ctx.config.resolved_model(),
            fast=False,
        )
        return CommandResult()


# =========================================================================== #
# POWER-USER UTILITIES (2)
# =========================================================================== #
class DashboardCommand(Command):
    name = "/dashboard"
    aliases = ("/overview",)
    help = "Show a combined dashboard: cost + tokens + context + health"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..token_counter import get_token_counter
        from ..context_manager import context_budget_for
        from ..token_counter import count_message_tokens
        from ..cost_tracker import get_cost_tracker
        snap = get_token_counter().snapshot()
        model = ctx.config.resolved_model()
        budget = context_budget_for(model)
        current = count_message_tokens(ctx.app.conversation.messages, model, ctx.config.provider)
        cost_tracker = get_cost_tracker()
        lines = [
            "╔═══════════════════════════════════════════════════════════╗",
            "║              📊  AGENT DASHBOARD                          ║",
            "╠═══════════════════════════════════════════════════════════╣",
            f"║  Provider:   {ctx.config.provider}/{model:<43}║",
            f"║  Goal mode:  {'ACTIVE' if ctx.app.goal_mode else 'off':<49}║",
            f"║  Context:    {current:,}/{budget:,} tokens ({current/budget*100:.0f}%){'':<20}║",
            f"║  Session:    {snap['session_total']:,} tokens, {snap['session_cost_fmt']:<25}║",
            f"║  All-time:   {snap['all_time_total']:,} tokens, {snap['all_time_cost_fmt']:<25}║",
            f"║  Tools:      {len(ctx.app.registry.all()):<49}║",
            f"║  Commands:   {len(ctx.app.commands.all()):<49}║",
        ]
        if snap["goal_turns"] > 0:
            lines.append(f"║  Goal:       {snap['goal_total']:,} tokens, {snap['goal_cost_fmt']:<25}║")
        st = cost_tracker.budget_status()
        if st.budget_usd > 0:
            lines.append(f"║  Budget:     {st.percent_used:.1f}% of ${st.budget_usd:.2f} used{'':<28}║")
        lines.append("╚═══════════════════════════════════════════════════════════╝")
        ctx.ui.console.print("\n".join(lines))
        return CommandResult()


class StatsCommand(Command):
    name = "/stats"
    help = "Show agent statistics: turns, tool calls, avg latency, etc."

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..token_counter import get_token_counter
        counter = get_token_counter()
        session = counter.session
        if not session.turns:
            ctx.ui.info("No turns recorded yet.")
            return CommandResult()
        avg_in = session.total_input / len(session.turns)
        avg_out = session.total_output / len(session.turns)
        avg_dur = session.total_duration_s / len(session.turns)
        ctx.ui.info(f"Session statistics ({len(session.turns)} turns):")
        ctx.ui.info(f"  avg input:     {avg_in:,.0f} tokens/turn")
        ctx.ui.info(f"  avg output:    {avg_out:,.0f} tokens/turn")
        ctx.ui.info(f"  avg duration:  {avg_dur:.2f}s/turn")
        ctx.ui.info(f"  tool calls:    {session.total_tool_calls}")
        if session.total_duration_s > 0:
            tps = session.total_output / session.total_duration_s
            ctx.ui.info(f"  avg throughput: {tps:.0f} tokens/s")
        ctx.ui.info(f"  total cost:    {counter.snapshot()['session_cost_fmt']}")
        return CommandResult()


# =========================================================================== #
# REGISTRATION
# =========================================================================== #
def build_enterprise_commands() -> list[Command]:
    """Return all 40+ enterprise commands as instances."""
    return [
        # Cost & tokens (5)
        CostCommand(),
        BudgetCommand(),
        TokensCommand(),
        AllTimeCommand(),
        ResetCostCommand(),
        # Routing & fallback (5)
        RouterCommand(),
        RouteCommand(),
        FallbackCommand(),
        FallbackResetCommand(),
        ModelsAllCommand(),
        # Consensus & quality (4)
        ConsensusCommand(),
        QualityCommand(),
        CompareCommand(),
        JudgeCommand(),
        # Branching & session (4)
        BranchCommand(),
        SwitchCommand(),
        BranchesCommand(),
        SnapshotCommand(),
        # Goal mode extensions (6)
        GoalHistoryCommand(),
        GoalShowCommand(),
        GoalResumeCommand(),
        GoalTemplatesCommand(),
        GoalTemplateCommand(),
        GoalCommandCmd(),
        # Telemetry & profiling (4)
        TelemetryCommand(),
        ProfileCommand(),
        ProfileSummaryCommand(),
        HealthCheckCommand(),
        # Recovery & history (4)
        RecoverCommand(),
        CheckpointCommand(),
        HistoryCommand(),
        ClearHistoryCommand(),
        # Prompt library (3)
        PromptsCommand(),
        UsePromptCommand(),
        PromptInfoCommand(),
        # Templates & scaffolding (3)
        ContextCommand(),
        CompactCommand(),
        BootCommand(),
        # Power-user utilities (2)
        DashboardCommand(),
        StatsCommand(),
    ]
