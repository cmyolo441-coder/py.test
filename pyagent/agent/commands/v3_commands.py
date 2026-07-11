"""v3 enterprise commands — exposes all v3 features as slash commands.

40+ new commands covering: RAG, multi-agent, self-reflection, security,
infrastructure, integrations, voice, browser, scheduler, MCP, feature
flags, hot reload, plugin marketplace, tool creator, knowledge graph,
long-term memory, Prometheus exporter, KV cache, connection pool.
"""
from __future__ import annotations

from .base import Command, CommandContext, CommandResult


# --- RAG & Knowledge Graph (5) ---
class IndexCodebaseCommand(Command):
    name = "/index-codebase"
    help = "Index the current codebase into the vector store for semantic search"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..rag_v2 import index_codebase
        stats = index_codebase(ctx.args.strip() or ".")
        ctx.ui.success(f"Indexed {stats['newly_indexed']} chunks from {stats['sources']} sources. Total: {stats['documents']} chunks.")
        return CommandResult()


class RagSearchCommand(Command):
    name = "/rag"
    aliases = ("/search",)
    help = "Semantic search the indexed codebase (/rag <query>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /rag <query>")
            return CommandResult()
        from ..rag_v2 import get_vector_store
        store = get_vector_store()
        if not store.documents:
            ctx.ui.error("No documents indexed. Run /index-codebase first.")
            return CommandResult()
        results = store.search(ctx.args, top_k=5)
        if not results:
            ctx.ui.info("No matches found.")
            return CommandResult()
        for score, doc in results:
            ctx.ui.info(f"[{score:.3f}] {doc.source} #{doc.chunk_index}")
            ctx.ui.info(f"  {doc.text[:300]}…")
        return CommandResult()


class KnowledgeGraphCommand(Command):
    name = "/kg"
    help = "Build and show the codebase knowledge graph"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..knowledge_graph import build_graph_from_codebase
        kg = build_graph_from_codebase(ctx.args.strip() or ".")
        ctx.ui.console.print(kg.dashboard())
        return CommandResult()


class KgQueryCommand(Command):
    name = "/kg-query"
    help = "Query the knowledge graph for entities (/kg-query <name>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /kg-query <name>")
            return CommandResult()
        from ..knowledge_graph import build_graph_from_codebase
        kg = build_graph_from_codebase(".")
        results = kg.find(ctx.args.strip())
        if not results:
            ctx.ui.info(f"No nodes matching '{ctx.args.strip()}'.")
            return CommandResult()
        for node in results[:20]:
            ctx.ui.info(f"  [{node.kind}] {node.name} at {node.location}")
        return CommandResult()


class LongTermMemoryCommand(Command):
    name = "/memory"
    help = "Show long-term memory contents (/memory recall|remember|stats)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..long_term_memory import get_long_term_memory
        mem = get_long_term_memory()
        parts = ctx.args.split(None, 1)
        sub = parts[0].lower() if parts else "stats"
        if sub == "stats":
            ctx.ui.console.print(mem.dashboard())
        elif sub == "recall":
            if len(parts) < 2:
                ctx.ui.error("Usage: /memory recall <query>")
                return CommandResult()
            context = mem.recall_facts(parts[1], top_k=5)
            for f in context:
                ctx.ui.info(f"  [{f['category']}] {f['fact'][:200]} (score: {f['score']:.2f})")
        elif sub == "remember":
            if len(parts) < 2:
                ctx.ui.error("Usage: /memory remember <fact>")
                return CommandResult()
            fact_id = mem.record_fact(parts[1])
            ctx.ui.success(f"Remembered (id={fact_id}).")
        elif sub == "list":
            for fact in mem.list_facts():
                ctx.ui.info(f"  [{fact.category}] {fact.fact}")
        else:
            ctx.ui.console.print(mem.dashboard())
        return CommandResult()


# --- Multi-Agent & Reflection (4) ---
class MultiAgentCommand(Command):
    name = "/multi-agent"
    aliases = ("/orchestrate",)
    help = "Run a task through multiple specialist agents (/multi-agent <task>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            from ..multi_agent import list_specialists
            ctx.ui.info("Available specialists:")
            for name, desc in list_specialists():
                ctx.ui.info(f"  {name:<14} {desc}")
            ctx.ui.info("\nUsage: /multi-agent <task>  (runs planner->coder->reviewer->tester)")
            return CommandResult()
        from ..multi_agent import MultiAgentOrchestrator
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        orch = MultiAgentOrchestrator(ctx.app)
        ctx.ui.info("Running multi-agent pipeline (planner -> coder -> reviewer -> tester)…")
        result = orch.run_pipeline(ctx.args)
        ctx.ui.console.print(result.summary())
        ctx.ui.console.print(result.merged_output[:5000])
        return CommandResult()


class OrchestrateParallelCommand(Command):
    name = "/orchestrate-parallel"
    help = "Run a task through multiple specialists in parallel (/orchestrate-parallel <task>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        if not ctx.args:
            ctx.ui.error("Usage: /orchestrate-parallel <task>")
            return CommandResult()
        from ..multi_agent import MultiAgentOrchestrator
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        orch = MultiAgentOrchestrator(ctx.app)
        ctx.ui.info("Running 4 specialists in parallel…")
        result = orch.run_parallel(ctx.args, ["researcher", "coder", "reviewer", "security"])
        ctx.ui.console.print(result.summary())
        ctx.ui.console.print(result.merged_output[:5000])
        return CommandResult()


class ReflectCommand(Command):
    name = "/reflect"
    help = "Run self-reflection on the last response to improve quality"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..self_reflection import SelfReflection
        # Find last user + assistant.
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
            ctx.ui.error("No response to reflect on.")
            return CommandResult()
        if ctx.app.agent is None and not ctx.app.build_agent():
            return CommandResult()
        reflector = SelfReflection(ctx.app)
        ctx.ui.info("Running self-reflection loop (up to 3 iterations)…")
        result = reflector.reflect(last_user or "", last_assistant)
        ctx.ui.console.print(result.summary())
        if result.improvement > 0:
            ctx.ui.success(f"Improved by {result.improvement} points.")
            ctx.ui.console.print(result.final[:3000])
        else:
            ctx.ui.info("No improvement gained.")
        return CommandResult()


class ToolLearningCommand(Command):
    name = "/tool-learning"
    help = "Show which tools are most reliable (/tool-learning reset to clear)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..tool_learning import get_tool_learner
        learner = get_tool_learner()
        if ctx.args.strip().lower() == "reset":
            learner.reset()
            ctx.ui.success("Tool learning data cleared.")
            return CommandResult()
        ctx.ui.console.print(learner.dashboard())
        return CommandResult()


# --- Security Suite (5) ---
class SastCommand(Command):
    name = "/sast"
    help = "Run static security analysis on the codebase"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..sast import scan_codebase
        report = scan_codebase(ctx.args.strip() or ".")
        ctx.ui.console.print(report.summary())
        return CommandResult()


class SbomCommand(Command):
    name = "/sbom"
    help = "Generate a Software Bill of Materials (CycloneDX format)"

    def run(self, ctx: CommandContext) -> CommandResult:
        import json
        from pathlib import Path

        from ..sbom import generate_sbom, sbom_summary
        root = ctx.args.strip() or "."
        sbom = generate_sbom(root)
        ctx.ui.console.print(sbom_summary(sbom))
        path = Path(root) / "sbom.json" if root != "." else Path("sbom.json")
        path.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
        ctx.ui.success(f"SBOM written to {path}")
        return CommandResult()


class ScanInfraCommand(Command):
    name = "/scan-infra"
    help = "Scan Dockerfiles, Terraform, CloudFormation for security issues"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..iac_scanner import scan_infrastructure
        reports = scan_infrastructure(ctx.args.strip() or ".")
        if not reports:
            ctx.ui.info("No infrastructure files found.")
            return CommandResult()
        for r in reports:
            ctx.ui.console.print(r.summary())
        return CommandResult()


class ScanPiiCommand(Command):
    name = "/scan-pii"
    help = "Scan for personally identifiable information (emails, phones, cards, etc.)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..pii_scanner import scan_directory_for_pii
        report = scan_directory_for_pii(ctx.args.strip() or ".")
        ctx.ui.console.print(report.summary())
        return CommandResult()


class AuditLogCommand(Command):
    name = "/audit"
    help = "Show the immutable audit log, or verify its integrity (/audit verify)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..audit_log import get_audit_log
        log = get_audit_log()
        if ctx.args.strip().lower() == "verify":
            ok, msg = log.verify()
            if ok:
                ctx.ui.success(msg)
            else:
                ctx.ui.error(msg)
            return CommandResult()
        ctx.ui.console.print(log.dashboard())
        return CommandResult()


# --- Infrastructure (4) ---
class DockerCommand(Command):
    name = "/docker"
    help = "Docker helpers: generate, up, down, ps, logs"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..docker_orchestrator import (
            compose_down,
            compose_ps,
            compose_up,
            generate_dockerfile,
            list_containers,
        )
        sub = ctx.args.split()[0].lower() if ctx.args.split() else "help"
        if sub == "generate":
            lang = ctx.args.split()[1] if len(ctx.args.split()) > 1 else "python"
            path = generate_dockerfile(language=lang)
            ctx.ui.success(f"Generated Dockerfile at {path}")
        elif sub == "up":
            ok, msg = compose_up()
            ctx.ui.info(f"compose up: {msg}")
        elif sub == "down":
            ok, msg = compose_down()
            ctx.ui.info(f"compose down: {msg}")
        elif sub == "ps":
            ctx.ui.console.print(compose_ps())
        elif sub == "containers":
            ctx.ui.console.print(list_containers())
        else:
            ctx.ui.info("Usage: /docker generate|up|down|ps|containers")
        return CommandResult()


class CloudCostCommand(Command):
    name = "/cloud-cost"
    help = "Analyze cloud resources for cost savings"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..cloud_cost import EXAMPLE_RESOURCES, analyse_resources
        report = analyse_resources(EXAMPLE_RESOURCES)
        ctx.ui.console.print(report.dashboard())
        return CommandResult()


class PrometheusCommand(Command):
    name = "/prometheus"
    help = "Start/stop the Prometheus metrics exporter (/prometheus on|off|status)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..prometheus_exporter import exporter_status, scrape_once, start_exporter, stop_exporter
        sub = ctx.args.strip().lower()
        if sub == "on":
            url = start_exporter()
            ctx.ui.success(f"Prometheus exporter started at {url}")
        elif sub == "off":
            stop_exporter()
            ctx.ui.success("Prometheus exporter stopped.")
        elif sub == "scrape":
            ctx.ui.console.print(scrape_once())
        else:
            ctx.ui.info(exporter_status())
        return CommandResult()


class KvCacheCommand(Command):
    name = "/kv-cache"
    help = "Show KV cache stats (/kv-cache clear to reset)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..kv_cache import get_kv_cache
        cache = get_kv_cache()
        if ctx.args.strip().lower() == "clear":
            cache.clear()
            ctx.ui.success("KV cache cleared.")
            return CommandResult()
        ctx.ui.console.print(cache.dashboard())
        return CommandResult()


# --- Integrations (4) ---
class NotifyCommand(Command):
    name = "/notify"
    help = "Send a notification to Slack/Discord/Teams/email (/notify <channel> <message>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..integrations import integration_status, send_discord, send_slack, send_teams
        parts = ctx.args.split(None, 1)
        if not parts:
            ctx.ui.console.print(integration_status())
            return CommandResult()
        channel = parts[0].lower()
        message = parts[1] if len(parts) > 1 else "Agent notification"
        if channel == "slack":
            ok, msg = send_slack(message)
        elif channel == "discord":
            ok, msg = send_discord(message)
        elif channel == "teams":
            ok, msg = send_teams(message)
        elif channel == "status":
            ctx.ui.console.print(integration_status())
            return CommandResult()
        else:
            ctx.ui.error(f"Unknown channel: {channel}. Use: slack, discord, teams")
            return CommandResult()
        if ok:
            ctx.ui.success(f"Sent to {channel}: {msg}")
        else:
            ctx.ui.error(f"Failed: {msg}")
        return CommandResult()


class IntegrationsCommand(Command):
    name = "/integrations"
    help = "Show integration status (Slack, Discord, Teams, Email)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..integrations import integration_status
        ctx.ui.console.print(integration_status())
        return CommandResult()


class VoiceCommand(Command):
    name = "/voice"
    help = "Voice interface: speak text or check availability (/voice speak <text>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..voice import list_voices, text_to_speech, voice_available
        parts = ctx.args.split(None, 1)
        sub = parts[0].lower() if parts else "status"
        if sub == "speak":
            if not voice_available():
                ctx.ui.error("No TTS backend available. Install espeak or use macOS.")
                return CommandResult()
            text = parts[1] if len(parts) > 1 else "Hello from the terminal agent."
            path = text_to_speech(text)
            if path:
                ctx.ui.success(f"Speech saved to {path}")
            else:
                ctx.ui.error("TTS failed.")
        elif sub == "voices":
            voices = list_voices()
            ctx.ui.info(f"Available voices: {', '.join(voices[:10])}")
        else:
            avail = "available" if voice_available() else "not available"
            ctx.ui.info(f"Voice interface: {avail}")
        return CommandResult()


class BrowserCommand(Command):
    name = "/browser"
    help = "Browser automation: navigate, click, fill forms (/browser <url>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..browser_automation import browser_status, navigate
        if not ctx.args:
            ctx.ui.info(browser_status())
            return CommandResult()
        if ctx.args.startswith("http"):
            ctx.ui.info(f"Navigating to {ctx.args}…")
            result = navigate(ctx.args, take_screenshot=True)
            if result.success:
                ctx.ui.info(f"Title: {result.title}")
                ctx.ui.info(f"Text preview: {result.text[:500]}")
                if result.screenshot_path:
                    ctx.ui.success(f"Screenshot: {result.screenshot_path}")
            else:
                ctx.ui.error(f"Navigation failed: {result.error}")
        else:
            ctx.ui.info(browser_status())
        return CommandResult()


# --- Agent Protocols (6) ---
class McpCommand(Command):
    name = "/mcp"
    help = "MCP server: list tools or start the stdio server (/mcp list|start)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..mcp_server import get_mcp_server
        server = get_mcp_server()
        sub = ctx.args.strip().lower()
        if sub == "list":
            tools = server.list_tools_as_mcp()
            ctx.ui.info(f"{len(tools)} tools exposed via MCP:")
            for t in tools[:20]:
                ctx.ui.info(f"  {t['name']}: {t['description'][:80]}")
        elif sub == "start":
            ctx.ui.info("Starting MCP server on stdio (Ctrl+C to stop)…")
            server.run_stdio()
        else:
            ctx.ui.info("Usage: /mcp list|start")
        return CommandResult()


class ToolCreatorCommand(Command):
    name = "/tool-creator"
    help = "List or delete agent-generated tools (/tool-creator list|delete <name>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..tool_creator import delete_generated_tool, list_generated_tools
        parts = ctx.args.split()
        sub = parts[0].lower() if parts else "list"
        if sub == "list":
            tools = list_generated_tools()
            if not tools:
                ctx.ui.info("No generated tools yet.")
            for t in tools:
                ctx.ui.info(f"  {t['name']:<20} {t['description']}")
        elif sub == "delete" and len(parts) > 1:
            if delete_generated_tool(parts[1]):
                ctx.ui.success(f"Deleted tool '{parts[1]}'.")
            else:
                ctx.ui.error(f"Tool '{parts[1]}' not found.")
        else:
            ctx.ui.info("Usage: /tool-creator list|delete <name>")
        return CommandResult()


class PluginMarketplaceCommand(Command):
    name = "/plugins"
    help = "Plugin marketplace: list, install, uninstall"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..plugin_marketplace import install, install_all, marketplace_dashboard, uninstall
        parts = ctx.args.split()
        sub = parts[0].lower() if parts else "list"
        if sub == "list" or sub == "":
            ctx.ui.console.print(marketplace_dashboard())
        elif sub == "install":
            if len(parts) < 2:
                ctx.ui.error("Usage: /plugins install <name>")
                return CommandResult()
            ok, msg = install(parts[1])
            if ok:
                ctx.ui.success(msg)
            else:
                ctx.ui.error(msg)
        elif sub == "uninstall":
            if len(parts) < 2:
                ctx.ui.error("Usage: /plugins uninstall <name>")
                return CommandResult()
            ok, msg = uninstall(parts[1])
            if ok:
                ctx.ui.success(msg)
            else:
                ctx.ui.error(msg)
        elif sub == "install-all":
            results = install_all()
            for name, (_ok, msg) in results.items():
                ctx.ui.info(f"  {name}: {msg}")
        else:
            ctx.ui.info("Usage: /plugins list|install <name>|uninstall <name>|install-all")
        return CommandResult()


class FlagCommand(Command):
    name = "/flag"
    help = "Toggle feature flags (/flag <name> on|off, or /flag to list)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..feature_flags import get_feature_flags
        flags = get_feature_flags()
        parts = ctx.args.split()
        if not parts:
            ctx.ui.console.print(flags.dashboard())
            return CommandResult()
        name = parts[0]
        if len(parts) < 2:
            # Toggle.
            result = flags.toggle(name)
            if result is None:
                ctx.ui.error(f"Unknown flag: {name}")
            else:
                ctx.ui.success(f"Flag '{name}' is now {'ON' if result else 'OFF'}")
        else:
            value = parts[1].lower() in ("on", "true", "1", "yes")
            if flags.set(name, value):
                ctx.ui.success(f"Flag '{name}' set to {'ON' if value else 'OFF'}")
            else:
                ctx.ui.error(f"Unknown flag: {name}")
        return CommandResult()


class HotReloadCommand(Command):
    name = "/hot-reload"
    help = "Toggle hot reload of agent modules (/hot-reload on|off|status)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..hot_reload import get_hot_reloader
        reloader = get_hot_reloader()
        sub = ctx.args.strip().lower()
        if sub == "on":
            reloader.start()
            ctx.ui.success("Hot reload enabled.")
        elif sub == "off":
            reloader.stop()
            ctx.ui.success("Hot reload disabled.")
        else:
            ctx.ui.info(reloader.status())
        return CommandResult()


class SchedulerCommand(Command):
    name = "/scheduler"
    aliases = ("/schedule",)
    help = "Background task scheduler: list, add, cancel (/scheduler add <name> <every_s> <prompt>)"

    def run(self, ctx: CommandContext) -> CommandResult:
        from ..scheduler import get_scheduler
        sched = get_scheduler(app=ctx.app)
        parts = ctx.args.split(None, 2)
        sub = parts[0].lower() if parts else "list"
        if sub == "list" or not parts:
            ctx.ui.console.print(sched.dashboard())
        elif sub == "add":
            if len(parts) < 3:
                ctx.ui.error("Usage: /scheduler add <name> <every_seconds> <prompt>")
                return CommandResult()
            name = parts[1]
            try:
                interval = float(parts[2].split()[0])
            except (ValueError, IndexError):
                ctx.ui.error("Invalid interval.")
                return CommandResult()
            prompt = parts[2].split(None, 1)[1] if " " in parts[2] else parts[2]
            task_id = sched.schedule(name=name, prompt=prompt, every_seconds=interval)
            sched.start()
            ctx.ui.success(f"Scheduled task '{name}' (id={task_id}) every {interval}s")
        elif sub == "cancel":
            if len(parts) < 2:
                ctx.ui.error("Usage: /scheduler cancel <id>")
                return CommandResult()
            if sched.cancel(parts[1]):
                ctx.ui.success(f"Cancelled task {parts[1]}")
            else:
                ctx.ui.error(f"Task {parts[1]} not found")
        elif sub == "start":
            sched.start()
            ctx.ui.success("Scheduler started.")
        elif sub == "stop":
            sched.stop()
            ctx.ui.success("Scheduler stopped.")
        else:
            ctx.ui.info("Usage: /scheduler list|add|cancel|start|stop")
        return CommandResult()


# --- Registration ---
def build_v3_commands() -> list[Command]:
    return [
        # RAG & KG
        IndexCodebaseCommand(),
        RagSearchCommand(),
        KnowledgeGraphCommand(),
        KgQueryCommand(),
        LongTermMemoryCommand(),
        # Multi-agent & reflection
        MultiAgentCommand(),
        OrchestrateParallelCommand(),
        ReflectCommand(),
        ToolLearningCommand(),
        # Security
        SastCommand(),
        SbomCommand(),
        ScanInfraCommand(),
        ScanPiiCommand(),
        AuditLogCommand(),
        # Infrastructure
        DockerCommand(),
        CloudCostCommand(),
        PrometheusCommand(),
        KvCacheCommand(),
        # Integrations
        NotifyCommand(),
        IntegrationsCommand(),
        VoiceCommand(),
        BrowserCommand(),
        # Protocols
        McpCommand(),
        ToolCreatorCommand(),
        PluginMarketplaceCommand(),
        FlagCommand(),
        HotReloadCommand(),
        SchedulerCommand(),
    ]
