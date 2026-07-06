"""Tests for v3 enterprise features (RAG v2, multi-agent, self-reflection,
tool learning, long-term memory, knowledge graph, git workflow, CI/CD,
code metrics, SAST, SBOM, infrastructure scanner, PII scanner, audit log,
Docker, cloud cost, Prometheus, connection pool, integrations, vision,
voice, browser, scheduler, MCP, tool creator, plugin marketplace, feature
flags, hot reload, KV cache)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# --- RAG v2 ---
def test_rag_v2_hash_embedding():
    from agent.rag_v2 import _hash_embedding
    emb = _hash_embedding(["hello", "world"])
    assert len(emb) == 256
    # L2-normalised.
    import math
    norm = math.sqrt(sum(v * v for v in emb))
    assert abs(norm - 1.0) < 0.01


def test_rag_v2_cosine():
    from agent.rag_v2 import _cosine
    assert _cosine([1, 0], [1, 0]) == 1.0
    assert _cosine([1, 0], [0, 1]) == 0.0
    assert _cosine([1, 0], [-1, 0]) == -1.0


def test_rag_v2_add_and_search(tmp_path):
    from agent.rag_v2 import VectorStore
    store = VectorStore(persist_path=tmp_path / "vs.json", chunk_size=100, overlap=10)
    store.add_text("file1.py", "def hello(): return 'world'\n# This function greets the user")
    store.add_text("file2.py", "class Database: pass\n# Database connection class")
    results = store.search("greet function")
    assert len(results) > 0
    assert "hello" in results[0][1].text.lower() or "greet" in results[0][1].text.lower()


def test_rag_v2_add_file(tmp_path):
    from agent.rag_v2 import VectorStore
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    return 42\n", encoding="utf-8")
    store = VectorStore(persist_path=tmp_path / "vs.json")
    added = store.add_file(f)
    assert added >= 1


def test_rag_v2_persist_and_load(tmp_path):
    from agent.rag_v2 import VectorStore
    path = tmp_path / "vs.json"
    s1 = VectorStore(persist_path=path)
    s1.add_text("test.py", "hello world")
    s1.save()
    s2 = VectorStore(persist_path=path)
    assert len(s2.documents) == len(s1.documents)


def test_rag_v2_stats():
    from agent.rag_v2 import VectorStore
    store = VectorStore()
    store.add_text("f1.py", "hello world")
    stats = store.stats()
    assert "documents" in stats
    assert stats["documents"] >= 1


def test_rag_v2_answer_context():
    from agent.rag_v2 import VectorStore
    store = VectorStore()
    store.add_text("f.py", "def authenticate(user, password): check credentials")
    ctx = store.answer_context("how does auth work?")
    assert isinstance(ctx, str)


# --- Multi-agent ---
def test_multi_agent_specialists_list():
    from agent.multi_agent import list_specialists, SPECIALISTS
    specs = list_specialists()
    assert len(specs) >= 6
    names = [s[0] for s in specs]
    assert "coder" in names
    assert "reviewer" in names


def test_multi_agent_orchestration_result_summary():
    from agent.multi_agent import OrchestrationResult, SubAgentResult
    r = OrchestrationResult(
        task="test task",
        results=[SubAgentResult("coder", "write code", "done", 1.0)],
        merged_output="merged",
        total_duration_s=1.0,
    )
    s = r.summary()
    assert "test task" in s


# --- Self-reflection ---
def test_self_reflection_no_agent():
    from agent.self_reflection import SelfReflection, ReflectionResult
    # Without an agent, reflection should still return a result.
    reflector = SelfReflection(app=None)
    result = reflector.reflect("test", "test response", max_iterations=1)
    assert isinstance(result, ReflectionResult)
    assert result.original == "test response"


def test_self_reflection_result_summary():
    from agent.self_reflection import ReflectionResult, ReflectionIteration
    r = ReflectionResult(original="a", final="b")
    r.iterations = [
        ReflectionIteration(0, "a", "", 50),
        ReflectionIteration(1, "b", "fixed", 80),
    ]
    assert r.improvement == 30
    assert "30" in r.summary()


# --- Tool learning ---
def test_tool_learning_record_and_recommend(tmp_path):
    from agent.tool_learning import ToolLearner
    learner = ToolLearner(persist_path=tmp_path / "tl.json")
    learner.record("calculate", success=True, duration_s=0.1, query_class="math")
    learner.record("calculate", success=True, duration_s=0.1, query_class="math")
    learner.record("run_shell", success=False, duration_s=1.0, query_class="coding")
    recs = learner.recommend(query_class="math", available_tools=["calculate", "run_shell"])
    assert recs[0][0] == "calculate"


def test_tool_learning_dashboard(tmp_path):
    from agent.tool_learning import ToolLearner
    learner = ToolLearner(persist_path=tmp_path / "tl.json")
    learner.record("calculate", success=True, duration_s=0.1)
    dash = learner.dashboard()
    assert "calculate" in dash


def test_tool_learning_persistence(tmp_path):
    from agent.tool_learning import ToolLearner
    path = tmp_path / "tl.json"
    l1 = ToolLearner(persist_path=path)
    l1.record("calculate", success=True)
    l2 = ToolLearner(persist_path=path)
    assert "calculate" in l2._stats


# --- Long-term memory ---
def test_long_term_memory_record_fact(tmp_path, monkeypatch):
    from agent.long_term_memory import LongTermMemory
    from pathlib import Path
    mem = LongTermMemory()
    mem.semantic_store = type(mem.semantic_store)(persist_path=tmp_path / "sem.json", chunk_size=100, overlap=10)
    mem.episodic_store = type(mem.episodic_store)(persist_path=tmp_path / "ep.json", chunk_size=100, overlap=10)
    mem._facts = []
    fid = mem.record_fact("user prefers terse answers", category="preference")
    assert fid
    facts = mem.list_facts()
    assert len(facts) >= 1


def test_long_term_memory_record_episode(tmp_path):
    from agent.long_term_memory import LongTermMemory
    mem = LongTermMemory()
    mem.episodic_store = type(mem.episodic_store)(persist_path=tmp_path / "ep.json", chunk_size=100, overlap=10)
    mem._episodes = []
    eid = mem.record_episode("what is 2+2?", "4", provider="zen", model="mimo")
    assert eid


def test_long_term_memory_dashboard(tmp_path):
    from agent.long_term_memory import LongTermMemory
    mem = LongTermMemory()
    mem.semantic_store = type(mem.semantic_store)(persist_path=tmp_path / "sem.json", chunk_size=100, overlap=10)
    mem._facts = []
    mem.record_fact("test fact", category="lesson")
    dash = mem.dashboard()
    assert "Long-term" in dash


# --- Knowledge graph ---
def test_knowledge_graph_build(tmp_path):
    from agent.knowledge_graph import build_graph_from_codebase
    f = tmp_path / "mod.py"
    f.write_text("import os\n\nclass Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    pass\n", encoding="utf-8")
    kg = build_graph_from_codebase(tmp_path)
    assert len(kg.nodes) > 0
    assert any(n.kind == "file" for n in kg.nodes.values())
    assert any(n.kind == "class" for n in kg.nodes.values())


def test_knowledge_graph_find():
    from agent.knowledge_graph import KnowledgeGraph, GraphNode
    kg = KnowledgeGraph()
    kg.add_node(GraphNode(id="f:App", kind="class", name="App"))
    results = kg.find("App")
    assert len(results) == 1


def test_knowledge_graph_shortest_path():
    from agent.knowledge_graph import KnowledgeGraph, GraphNode, GraphEdge
    kg = KnowledgeGraph()
    kg.add_node(GraphNode(id="a", kind="file", name="a"))
    kg.add_node(GraphNode(id="b", kind="file", name="b"))
    kg.add_node(GraphNode(id="c", kind="file", name="c"))
    kg.add_edge(GraphEdge(source="a", target="b", kind="imports"))
    kg.add_edge(GraphEdge(source="b", target="c", kind="imports"))
    path = kg.shortest_path("a", "c")
    assert path == ["a", "b", "c"]


def test_knowledge_graph_stats(tmp_path):
    from agent.knowledge_graph import build_graph_from_codebase
    f = tmp_path / "mod.py"
    f.write_text("def foo(): pass\n", encoding="utf-8")
    kg = build_graph_from_codebase(tmp_path)
    stats = kg.stats()
    assert "nodes" in stats
    assert "edges" in stats


# --- Git workflow ---
def test_git_workflow_status():
    from agent.git_workflow import git_status_short
    s = git_status_short()
    assert isinstance(s, str)


def test_git_workflow_recent_commits():
    from agent.git_workflow import recent_commits
    c = recent_commits(3)
    assert isinstance(c, str)


# --- CI/CD builder ---
def test_cicd_generate_github(tmp_path):
    from agent.cicd_builder import generate_github_actions
    path = generate_github_actions(root=str(tmp_path))
    content = Path(path).read_text()
    assert "name: CI" in content
    assert "pytest" in content


def test_cicd_generate_gitlab(tmp_path):
    from agent.cicd_builder import generate_gitlab_ci
    path = generate_gitlab_ci(root=str(tmp_path))
    content = Path(path).read_text()
    assert "stages:" in content


def test_cicd_generate_all(tmp_path):
    from agent.cicd_builder import generate_all
    paths = generate_all(root=str(tmp_path))
    assert "github_actions" in paths
    assert "gitlab_ci" in paths
    assert "circleci" in paths
    assert "jenkins" in paths


# --- Code metrics ---
def test_code_metrics_analyze_file(tmp_path):
    from agent.code_metrics import analyze_file
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    if True:\n        for i in range(10):\n            pass\n# TODO: fix\n", encoding="utf-8")
    m = analyze_file(f)
    assert m.functions == 1
    assert m.max_complexity >= 3
    assert m.todos == 1


def test_code_metrics_analyze_codebase(tmp_path):
    from agent.code_metrics import analyze_codebase
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    pass\n\nclass Bar:\n    def method(self):\n        pass\n", encoding="utf-8")
    report = analyze_codebase(tmp_path)
    assert report.files_scanned == 1
    assert report.total_functions >= 2
    assert report.total_classes == 1


def test_code_metrics_dashboard(tmp_path):
    from agent.code_metrics import analyze_codebase
    report = analyze_codebase(tmp_path)
    dash = report.dashboard()
    assert "CODE METRICS" in dash


def test_code_metrics_suggest_refactoring(tmp_path):
    from agent.code_metrics import analyze_codebase, suggest_refactoring
    f = tmp_path / "big.py"
    f.write_text("def huge():\n    " + "\n    ".join(["if True: pass"] * 60) + "\n", encoding="utf-8")
    report = analyze_codebase(tmp_path)
    suggestions = suggest_refactoring(report)
    assert isinstance(suggestions, list)


# --- SAST ---
def test_sast_scan_sql_injection(tmp_path):
    from agent.sast import scan_file
    f = tmp_path / "mod.py"
    f.write_text("def query(uid):\n    cur.execute(f'SELECT * FROM users WHERE id = {uid}')\n", encoding="utf-8")
    findings = scan_file(f)
    assert any(f.rule == "sql-injection" for f in findings)


def test_sast_scan_eval(tmp_path):
    from agent.sast import scan_file
    f = tmp_path / "mod.py"
    f.write_text("x = eval(input('enter: '))\n", encoding="utf-8")
    findings = scan_file(f)
    assert any(f.rule == "eval-exec" for f in findings)


def test_sast_scan_hardcoded_secret(tmp_path):
    from agent.sast import scan_file
    f = tmp_path / "mod.py"
    f.write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    findings = scan_file(f)
    assert any(f.rule == "hardcoded-secret" for f in findings)


def test_sast_scan_clean_file(tmp_path):
    from agent.sast import scan_file
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    return 42\n", encoding="utf-8")
    findings = scan_file(f)
    assert len(findings) == 0


def test_sast_report_summary(tmp_path):
    from agent.sast import scan_codebase
    f = tmp_path / "mod.py"
    f.write_text("x = eval('1')\n", encoding="utf-8")
    report = scan_codebase(tmp_path)
    s = report.summary()
    assert "SAST" in s


# --- SBOM ---
def test_sbom_generate(tmp_path):
    from agent.sbom import generate_sbom
    # Create a requirements.txt.
    (tmp_path / "requirements.txt").write_text("rich>=13.0\nhttpx>=0.27\n", encoding="utf-8")
    sbom = generate_sbom(root=str(tmp_path))
    assert sbom["bomFormat"] == "CycloneDX"
    assert len(sbom["components"]) == 2
    assert any(c["name"] == "rich" for c in sbom["components"])


def test_sbom_summary(tmp_path):
    from agent.sbom import generate_sbom, sbom_summary
    (tmp_path / "requirements.txt").write_text("pytest>=8.0\n", encoding="utf-8")
    sbom = generate_sbom(root=str(tmp_path))
    s = sbom_summary(sbom)
    assert "pytest" in s


def test_sbom_write_to_disk(tmp_path):
    from agent.sbom import generate_sbom
    (tmp_path / "requirements.txt").write_text("rich\n", encoding="utf-8")
    out = tmp_path / "sbom.json"
    result = generate_sbom(root=str(tmp_path), output_path=out)
    assert out.exists()


# --- Infrastructure scanner ---
def test_iac_scan_dockerfile(tmp_path):
    from agent.iac_scanner import scan_dockerfile
    f = tmp_path / "Dockerfile"
    f.write_text("FROM python:latest\nENV API_KEY=secret123\nCMD [\"python\"]\n", encoding="utf-8")
    findings = scan_dockerfile(f)
    assert any(f.rule == "latest-tag" for f in findings)
    assert any(f.rule == "secret-in-env" for f in findings)
    assert any(f.rule == "no-healthcheck" for f in findings)


def test_iac_scan_terraform(tmp_path):
    from agent.iac_scanner import scan_terraform
    f = tmp_path / "main.tf"
    f.write_text('resource "aws_s3_bucket" "b" {\n  acl = "public-read"\n}\n', encoding="utf-8")
    findings = scan_terraform(f)
    assert any(f.rule == "public-bucket" for f in findings)


def test_iac_scan_infrastructure(tmp_path):
    from agent.iac_scanner import scan_infrastructure
    (tmp_path / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    reports = scan_infrastructure(tmp_path)
    assert len(reports) >= 1
    assert any(r.scanner == "Dockerfile" for r in reports)


# --- PII scanner ---
def test_pii_scan_email():
    from agent.pii_scanner import scan_text_for_pii
    findings = scan_text_for_pii("Contact me at john@example.com please.")
    assert any(f.pii_type == "email" for f in findings)


def test_pii_scan_phone():
    from agent.pii_scanner import scan_text_for_pii
    findings = scan_text_for_pii("Call +919876543210 for support.")
    assert any(f.pii_type == "phone_in" for f in findings)


def test_pii_scan_credit_card():
    from agent.pii_scanner import scan_text_for_pii
    # 4111 1111 1111 1111 is a valid Luhn test number.
    findings = scan_text_for_pii("Card: 4111 1111 1111 1111")
    assert any(f.pii_type == "credit_card" for f in findings)


def test_pii_scan_ssn():
    from agent.pii_scanner import scan_text_for_pii
    findings = scan_text_for_pii("SSN: 123-45-6789")
    assert any(f.pii_type == "ssn_us" for f in findings)


def test_pii_scan_directory(tmp_path):
    from agent.pii_scanner import scan_directory_for_pii
    f = tmp_path / "data.txt"
    f.write_text("Email: test@example.com\n", encoding="utf-8")
    report = scan_directory_for_pii(tmp_path)
    assert len(report.findings) >= 1


# --- Audit log ---
def test_audit_log_record_and_verify(tmp_path):
    from agent.audit_log import ImmutableAuditLog
    log = ImmutableAuditLog(persist_path=tmp_path / "audit.log")
    log.record("test_action", actor="test", detail="value")
    log.record("another_action")
    ok, msg = log.verify()
    assert ok
    assert len(log.entries) == 2


def test_audit_log_dashboard(tmp_path):
    from agent.audit_log import ImmutableAuditLog
    log = ImmutableAuditLog(persist_path=tmp_path / "audit.log")
    log.record("login", actor="user")
    dash = log.dashboard()
    assert "login" in dash


def test_audit_log_query(tmp_path):
    from agent.audit_log import ImmutableAuditLog
    log = ImmutableAuditLog(persist_path=tmp_path / "audit.log")
    log.record("action_a")
    log.record("action_b")
    log.record("action_a")
    results = log.query(action="action_a")
    assert len(results) == 2


# --- Docker orchestrator ---
def test_docker_generate_dockerfile(tmp_path):
    from agent.docker_orchestrator import generate_dockerfile
    path = generate_dockerfile(language="python", port=8000, root=str(tmp_path))
    content = Path(path).read_text()
    assert "FROM python" in content
    assert "HEALTHCHECK" in content
    assert "USER appuser" in content


def test_docker_generate_compose(tmp_path):
    from agent.docker_orchestrator import generate_compose, ComposeService
    services = [ComposeService(name="web", image="nginx:latest", ports=["80:80"])]
    content = generate_compose(services)
    assert "web:" in content
    assert "nginx:latest" in content


# --- Cloud cost ---
def test_cloud_cost_analyse():
    from agent.cloud_cost import analyse_resources, Resource
    resources = [
        Resource(id="i-1", type="ec2", monthly_cost_usd=100, utilisation_pct=5),  # idle
        Resource(id="i-2", type="ec2", monthly_cost_usd=200, utilisation_pct=80),  # RI candidate
    ]
    report = analyse_resources(resources)
    assert len(report.savings) >= 2
    assert report.total_monthly_saving > 0


def test_cloud_cost_dashboard():
    from agent.cloud_cost import analyse_resources, EXAMPLE_RESOURCES
    report = analyse_resources(EXAMPLE_RESOURCES)
    dash = report.dashboard()
    assert "CLOUD COST" in dash


# --- Prometheus exporter ---
def test_prometheus_scrape():
    from agent.prometheus_exporter import scrape_once
    output = scrape_once()
    assert "agent_" in output


def test_prometheus_status():
    from agent.prometheus_exporter import exporter_status
    s = exporter_status()
    assert "Prometheus" in s


# --- Connection pool ---
def test_connection_pool_get():
    from agent.connection_pool import get_http_client, pool_stats
    client = get_http_client()
    stats = pool_stats()
    assert stats["available"] is True


def test_connection_pool_stats():
    from agent.connection_pool import pool_stats
    stats = pool_stats()
    assert "library" in stats


# --- Integrations ---
def test_integration_status():
    from agent.integrations import integration_status
    s = integration_status()
    assert "Slack" in s
    assert "Discord" in s


def test_integration_slack_no_webhook():
    from agent.integrations import send_slack
    ok, msg = send_slack("test")
    assert not ok
    assert "SLACK_WEBHOOK_URL" in msg


def test_integration_webhook():
    from agent.integrations import send_webhook
    ok, msg = send_webhook("https://httpbin.org/post", {"test": True})
    # May fail due to network, but should not crash.
    assert isinstance(ok, bool)
    assert isinstance(msg, str)


# --- Vision ---
def test_vision_analyze_no_file():
    from agent.vision import analyze_image
    result = analyze_image(path="/nonexistent.png")
    assert "not found" in result.description.lower()


def test_vision_analyze_url():
    from agent.vision import analyze_image
    result = analyze_image(url="https://example.com/image.png")
    assert isinstance(result.description, str)


# --- Voice ---
def test_voice_available():
    from agent.voice import voice_available
    assert isinstance(voice_available(), bool)


def test_voice_status():
    from agent.voice import voice_available
    # Just ensure it doesn't crash.
    _ = voice_available()


# --- Browser automation ---
def test_browser_status():
    from agent.browser_automation import browser_status
    s = browser_status()
    assert "Playwright" in s


def test_browser_fallback_fetch():
    from agent.browser_automation import navigate
    # This will use the httpx fallback if Playwright isn't installed.
    result = navigate("https://example.com")
    # May succeed or fail depending on network; just ensure it returns a BrowserResult.
    assert hasattr(result, "success")


# --- Scheduler ---
def test_scheduler_schedule_and_list():
    from agent.scheduler import Scheduler
    sched = Scheduler(app=None)
    task_id = sched.schedule("test", "do something", every_seconds=60)
    tasks = sched.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == task_id


def test_scheduler_cancel():
    from agent.scheduler import Scheduler
    sched = Scheduler(app=None)
    task_id = sched.schedule("test", "do something", every_seconds=60)
    assert sched.cancel(task_id) is True
    assert len(sched.list_tasks()) == 0


def test_scheduler_parse_cron():
    from agent.scheduler import _parse_cron
    assert _parse_cron("hourly") == 3600.0
    assert _parse_cron("daily") == 86400.0
    assert _parse_cron("every 30 minutes") == 1800.0
    assert _parse_cron("every 2 hours") == 7200.0


# --- MCP server ---
def test_mcp_list_tools():
    from agent.mcp_server import get_mcp_server
    server = get_mcp_server()
    tools = server.list_tools_as_mcp()
    assert len(tools) > 0
    assert all("name" in t and "description" in t for t in tools)


def test_mcp_initialize():
    from agent.mcp_server import get_mcp_server
    server = get_mcp_server()
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["serverInfo"]["name"] == "terminal-agent-mcp"


def test_mcp_tools_list():
    from agent.mcp_server import get_mcp_server
    server = get_mcp_server()
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert "tools" in resp["result"]


def test_mcp_call_tool():
    from agent.mcp_server import get_mcp_server
    server = get_mcp_server()
    resp = server.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "calculate", "arguments": {"expression": "2+2"}},
    })
    assert "result" in resp


# --- Tool creator ---
def test_tool_creator_validate_good():
    from agent.tool_creator import validate_tool_code
    code = "def run():\n    return 'hello'\n"
    ok, msg = validate_tool_code(code)
    assert ok


def test_tool_creator_validate_syntax_error():
    from agent.tool_creator import validate_tool_code
    code = "def run(:\n    pass\n"
    ok, msg = validate_tool_code(code)
    assert not ok


def test_tool_creator_validate_blocked_os_system():
    from agent.tool_creator import validate_tool_code
    code = "import os\ndef run():\n    os.system('rm -rf /')\n"
    ok, msg = validate_tool_code(code)
    assert not ok


def test_tool_creator_suggest():
    from agent.tool_creator import suggest_tool_for_task
    code = suggest_tool_for_task("count words in a file")
    assert "def run" in code


# --- Plugin marketplace ---
def test_plugin_marketplace_dashboard():
    from agent.plugin_marketplace import marketplace_dashboard
    dash = marketplace_dashboard()
    assert "Plugin marketplace" in dash


def test_plugin_marketplace_list():
    from agent.plugin_marketplace import list_available, list_installed
    available = list_available()
    installed = list_installed()
    assert isinstance(available, list)
    assert isinstance(installed, list)


# --- Feature flags ---
def test_feature_flags_defaults():
    from agent.feature_flags import FeatureFlags
    flags = FeatureFlags(persist_path=tempfile.mktemp(suffix=".json"))
    assert flags.is_enabled("auto_compact") is True
    assert flags.is_enabled("rag_v2") is False


def test_feature_flags_toggle(tmp_path):
    from agent.feature_flags import FeatureFlags
    flags = FeatureFlags(persist_path=tmp_path / "ff.json")
    flags.enable("rag_v2")
    assert flags.is_enabled("rag_v2") is True
    flags.disable("rag_v2")
    assert flags.is_enabled("rag_v2") is False


def test_feature_flags_persistence(tmp_path):
    from agent.feature_flags import FeatureFlags
    path = tmp_path / "ff.json"
    f1 = FeatureFlags(persist_path=path)
    f1.enable("multi_agent")
    f2 = FeatureFlags(persist_path=path)
    assert f2.is_enabled("multi_agent") is True


def test_feature_flags_dashboard():
    from agent.feature_flags import get_feature_flags
    flags = get_feature_flags()
    dash = flags.dashboard()
    assert "Feature flags" in dash


def test_feature_flags_unknown():
    from agent.feature_flags import FeatureFlags
    flags = FeatureFlags(persist_path=tempfile.mktemp(suffix=".json"))
    assert flags.enable("nonexistent_flag") is False
    assert flags.is_enabled("nonexistent_flag") is False


# --- Hot reload ---
def test_hot_reload_status():
    from agent.hot_reload import HotReloader
    r = HotReloader()
    s = r.status()
    assert "Hot reload" in s


def test_hot_reload_toggle():
    from agent.hot_reload import HotReloader
    r = HotReloader(watch_dir="/tmp")
    assert r.toggle() is True  # start
    assert r.toggle() is False  # stop


# --- KV cache ---
def test_kv_cache_set_and_get():
    from agent.kv_cache import KvCache
    cache = KvCache()
    cache.set([{"role": "user", "content": "hi"}], "hello!")
    result = cache.get([{"role": "user", "content": "hi"}])
    assert result == "hello!"


def test_kv_cache_miss():
    from agent.kv_cache import KvCache
    cache = KvCache()
    result = cache.get([{"role": "user", "content": "nope"}])
    assert result is None


def test_kv_cache_stats():
    from agent.kv_cache import KvCache
    cache = KvCache()
    cache.set([{"role": "user", "content": "hi"}], "hello")
    cache.get([{"role": "user", "content": "hi"}])
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["entries"] == 1


def test_kv_cache_clear():
    from agent.kv_cache import KvCache
    cache = KvCache()
    cache.set([{"role": "user", "content": "hi"}], "hello")
    cache.clear()
    assert cache.stats()["entries"] == 0


# --- v3 commands and tools registration ---
def test_v3_commands_built():
    from agent.commands.v3_commands import build_v3_commands
    cmds = build_v3_commands()
    assert len(cmds) >= 28
    names = [c.name for c in cmds]
    assert "/sast" in names
    assert "/rag" in names
    assert "/mcp" in names
    assert "/flag" in names


def test_v3_tools_built():
    from agent.tools.v3_tools import get_v3_tools
    tools = get_v3_tools()
    assert len(tools) >= 17
    names = [t.name for t in tools]
    assert "rag_search" in names
    assert "sast_scan" in names
    assert "browser_navigate" in names


def test_full_registry_has_v3_commands():
    from agent.commands import build_command_registry
    reg = build_command_registry()
    names = [c.name for c in reg.all()]
    assert "/sast" in names
    assert "/rag" in names
    assert "/mcp" in names
    assert "/flag" in names
    assert "/scheduler" in names


def test_full_registry_has_v3_tools():
    from agent.tools import build_default_registry
    reg = build_default_registry()
    names = [t.name for t in reg.all()]
    assert "rag_search" in names
    assert "sast_scan" in names
    assert "generate_sbom" in names
    assert "browser_navigate" in names
    assert "knowledge_graph_build" in names
