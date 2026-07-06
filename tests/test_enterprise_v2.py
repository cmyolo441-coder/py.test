"""Tests for the enterprise v2 subsystems (token counter, context manager,
fallback, router, consensus, quality scorer, branching, cost tracker,
telemetry, profiler, recovery, goal history, prompt library, goal templates,
widgets, new tool groups, enterprise commands)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Token counter
# ---------------------------------------------------------------------------
def test_count_tokens_nonempty():
    from agent.token_counter import count_tokens
    assert count_tokens("hello world this is a test", "gpt-4o", "openai") > 0


def test_count_tokens_empty():
    from agent.token_counter import count_tokens
    assert count_tokens("", "gpt-4o", "openai") == 0


def test_count_tokens_provider_strategy():
    from agent.token_counter import count_tokens
    # Anthropic strategy should work even without tiktoken.
    assert count_tokens("hello world", "claude-3-5-sonnet-20241022", "anthropic") > 0
    # Ollama strategy.
    assert count_tokens("hello world", "llama3.1", "ollama") > 0


def test_estimate_cost_known_model():
    from agent.token_counter import estimate_cost_usd
    assert estimate_cost_usd("gpt-4o", 1000, 1000) == 0.005 + 0.015


def test_estimate_cost_free_model():
    from agent.token_counter import estimate_cost_usd
    assert estimate_cost_usd("mimo-v2.5-free", 1000, 1000) == 0.0


def test_estimate_cost_inr():
    from agent.token_counter import estimate_cost_inr, USD_TO_INR
    usd = estimate_cost_inr("gpt-4o", 1000, 0)
    assert abs(usd - 0.005 * USD_TO_INR) < 0.01


def test_format_cost_free():
    from agent.token_counter import format_cost
    assert format_cost(0.0) == "free"


def test_format_cost_small():
    from agent.token_counter import format_cost
    s = format_cost(0.005)
    assert "$" in s and "₹" in s


def test_token_counter_record_and_snapshot(tmp_path):
    from agent.token_counter import TokenCounter
    counter = TokenCounter(persist_path=tmp_path / "usage.json")
    counter.record_turn("gpt-4o", "openai", 100, 50, duration_s=1.0, tool_calls=2)
    snap = counter.snapshot()
    assert snap["session_turns"] == 1
    assert snap["session_input"] == 100
    assert snap["session_output"] == 50
    assert snap["session_total"] == 150
    assert snap["session_cost_usd"] > 0


def test_token_counter_goal_tracking(tmp_path):
    from agent.token_counter import TokenCounter
    counter = TokenCounter(persist_path=tmp_path / "usage.json")
    counter.record_turn("gpt-4o", "openai", 100, 50, is_goal=True)
    counter.record_turn("gpt-4o", "openai", 200, 100, is_goal=True)
    snap = counter.snapshot()
    assert snap["goal_turns"] == 2
    assert snap["goal_total"] == 450
    counter.reset_goal()
    assert counter.snapshot()["goal_turns"] == 0


def test_token_counter_persistence(tmp_path):
    from agent.token_counter import TokenCounter
    path = tmp_path / "usage.json"
    c1 = TokenCounter(persist_path=path)
    c1.record_turn("gpt-4o", "openai", 100, 50)
    c2 = TokenCounter(persist_path=path)
    assert c2.snapshot()["all_time_turns"] >= 1


def test_turn_usage_properties():
    from agent.token_counter import TurnUsage
    t = TurnUsage(model="gpt-4o", provider="openai", input_tokens=100, output_tokens=50, duration_s=2.0)
    assert t.total_tokens == 150
    assert t.tokens_per_second == 25.0


def test_session_usage_summary():
    from agent.token_counter import SessionUsage, TurnUsage
    s = SessionUsage()
    s.record(TurnUsage(model="m", provider="p", input_tokens=10, output_tokens=5))
    assert "turns=1" in s.summary()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------
def test_context_budget_known_model():
    from agent.context_manager import context_budget_for
    assert context_budget_for("gpt-4o") == 110_000
    assert context_budget_for("claude-3-5-sonnet-20241022") == 160_000
    assert context_budget_for("gemini-1.5-pro") == 1_800_000


def test_context_budget_unknown_model():
    from agent.context_manager import context_budget_for, DEFAULT_BUDGET
    assert context_budget_for("totally-unknown-model") == DEFAULT_BUDGET


def test_compress_messages_no_trimming_needed():
    from agent.context_manager import compress_messages
    msgs = [{"role": "user", "content": "hi"}]
    assert compress_messages(msgs, "gpt-4o", "openai") == msgs


def test_compress_messages_trims_large_conversation():
    from agent.context_manager import compress_messages
    # Build a conversation that exceeds gpt-4o's 110k budget — impossible
    # with small messages, so use a tiny budget instead.
    msgs = [{"role": "user", "content": f"message number {i} " * 100} for i in range(50)]
    result = compress_messages(msgs, "gpt-4o", "openai", target_tokens=500, preserve_recent=4)
    assert len(result) < len(msgs)


def test_compress_preserves_system_prompt():
    from agent.context_manager import compress_messages
    msgs = [{"role": "system", "content": "you are helpful"}]
    msgs += [{"role": "user", "content": f"msg {i} " * 200} for i in range(20)]
    result = compress_messages(msgs, "gpt-4o", "openai", target_tokens=500, preserve_recent=3)
    assert result[0]["role"] == "system"


# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------
def test_fallback_chain_default():
    from agent.fallback import FallbackChain
    chain = FallbackChain()
    assert len(chain.entries) >= 4
    assert chain.entries[0].provider == "zen"


def test_fallback_chain_pick():
    from agent.fallback import FallbackChain
    chain = FallbackChain(["zen:mimo-v2.5-free", "ollama:llama3.1"])
    entry = chain.pick()
    assert entry is not None
    assert entry.provider in ("zen", "ollama")


def test_fallback_chain_failure_and_cooldown():
    import time
    from agent.fallback import FallbackChain, COOLDOWN_SECONDS
    chain = FallbackChain(["zen:mimo-v2.5-free", "ollama:llama3.1"])
    chain.record_failure("zen", "mimo-v2.5-free")
    # The zen entry should now be on cooldown.
    zen_entry = next(e for e in chain.entries if e.provider == "zen")
    assert zen_entry.is_on_cooldown
    assert zen_entry.failure_count == 1


def test_fallback_chain_health_table():
    from agent.fallback import FallbackChain
    chain = FallbackChain(["zen:mimo-v2.5-free", "ollama:llama3.1"])
    table = chain.health_table()
    assert len(table) == 2
    assert "provider" in table[0]


def test_fallback_chain_describe():
    from agent.fallback import FallbackChain
    chain = FallbackChain(["zen:mimo-v2.5-free"])
    desc = chain.describe()
    assert "Fallback chain:" in desc
    assert "zen" in desc


# ---------------------------------------------------------------------------
# Model router
# ---------------------------------------------------------------------------
def test_classify_query_coding():
    from agent.model_router import classify_query
    cls, _ = classify_query("debug this python function")
    assert cls == "coding"


def test_classify_query_math():
    from agent.model_router import classify_query
    cls, _ = classify_query("calculate 2 + 2 and solve the equation")
    assert cls == "math"


def test_classify_query_quick():
    from agent.model_router import classify_query
    cls, _ = classify_query("what is the capital of France?")
    assert cls == "quick"


def test_classify_query_default():
    from agent.model_router import classify_query
    cls, _ = classify_query("hello there")
    assert cls == "default"


def test_route_returns_decision():
    from agent.model_router import route
    decision = route("debug this code", available_providers=["openai", "anthropic"])
    assert decision.query_class == "coding"
    assert decision.provider in ("openai", "anthropic")
    assert decision.model
    assert decision.reason


def test_route_with_preferred_provider():
    from agent.model_router import route
    decision = route("debug this code", available_providers=["openai", "anthropic"], preferred_provider="anthropic")
    assert decision.provider == "anthropic"


def test_describe_routing_table():
    from agent.model_router import describe_routing_table
    desc = describe_routing_table()
    assert "coding" in desc
    assert "quick" in desc


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------
def test_consensus_first_strategy():
    from agent.consensus import run_consensus

    def getter(p, m): return None
    def chat(provider, messages):
        return f"response from {provider}"

    result = run_consensus(
        getter, chat, [{"role": "user", "content": "hi"}],
        [("openai", "gpt-4o"), ("anthropic", "claude")],
        strategy="first",
    )
    assert result.text.startswith("response from")
    assert len(result.all_responses) == 2


def test_consensus_longest_strategy():
    from agent.consensus import run_consensus

    def getter(p, m): return None
    def chat(provider, messages):
        if provider == "openai":
            return "short"
        return "this is a much longer response with more detail"

    result = run_consensus(
        getter, chat, [{"role": "user", "content": "hi"}],
        [("openai", "gpt-4o"), ("anthropic", "claude")],
        strategy="longest",
    )
    assert "longer" in result.text


def test_consensus_all_fail():
    from agent.consensus import run_consensus

    def getter(p, m): return None
    def chat(provider, messages):
        raise RuntimeError("boom")

    result = run_consensus(
        getter, chat, [{"role": "user", "content": "hi"}],
        [("openai", "gpt-4o"), ("anthropic", "claude")],
    )
    assert "failed" in result.text.lower()


def test_describe_strategies():
    from agent.consensus import describe_strategies
    desc = describe_strategies()
    assert "first" in desc
    assert "majority" in desc


# ---------------------------------------------------------------------------
# Quality scorer
# ---------------------------------------------------------------------------
def test_quality_score_empty():
    from agent.quality_scorer import score_response
    s = score_response("what is 2+2?", "")
    assert s.score == 0


def test_quality_score_good_response():
    from agent.quality_scorer import score_response
    s = score_response("explain Python", "Python is a high-level programming language. It is widely used for web development, data science, and automation. Here are the key features:\n- Easy to learn\n- Cross-platform\n- Large ecosystem")
    assert s.score > 50


def test_quality_score_with_placeholders():
    from agent.quality_scorer import score_response
    s = score_response("write a function", "def foo():\n    # TODO: implement\n    pass")
    assert s.correctness < 80
    assert any("TODO" in n or "placeholder" in n for n in s.notes)


def test_quality_grade():
    from agent.quality_scorer import QualityScore
    s = QualityScore(score=95, completeness=90, correctness=95, clarity=100, actionability=90)
    assert s.grade == "A"
    s2 = QualityScore(score=50, completeness=50, correctness=50, clarity=50, actionability=50)
    assert s2.grade == "F"


# ---------------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------------
def test_branch_manager_initial():
    from agent.branching import BranchManager
    bm = BranchManager([{"role": "user", "content": "hi"}])
    assert bm.active_id == "main"
    assert len(bm.messages) == 1


def test_branch_fork():
    from agent.branching import BranchManager
    bm = BranchManager([{"role": "user", "content": "hi"}])
    branch = bm.fork(name="test-branch")
    assert branch.name == "test-branch"
    assert bm.active_id == branch.id
    assert len(bm.list_branches()) == 2


def test_branch_switch():
    from agent.branching import BranchManager
    bm = BranchManager([{"role": "user", "content": "hi"}])
    b = bm.fork(name="b2")
    bm.switch("main")
    assert bm.active_id == "main"
    bm.switch(b.id)
    assert bm.active_id == b.id


def test_branch_tree():
    from agent.branching import BranchManager
    bm = BranchManager([{"role": "user", "content": "hi"}])
    bm.fork(name="dev")
    tree = bm.tree()
    assert "main" in tree
    assert "dev" in tree


def test_branch_delete_main_fails():
    from agent.branching import BranchManager
    bm = BranchManager([{"role": "user", "content": "hi"}])
    assert bm.delete("main") is False


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------
def test_cost_tracker_no_budget():
    from agent.cost_tracker import CostTracker
    from agent.token_counter import TokenCounter
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ct = CostTracker(counter=TokenCounter(persist_path=Path(td) / "u.json"))
        st = ct.budget_status()
        assert st.budget_usd == 0
        assert not st.exceeded


def test_cost_tracker_with_budget():
    from agent.cost_tracker import CostTracker
    from agent.token_counter import TokenCounter
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        counter = TokenCounter(persist_path=Path(td) / "u.json")
        counter.record_turn("gpt-4o", "openai", 1000, 1000)  # costs $0.02
        ct = CostTracker(counter=counter, budget_usd=1.0)
        st = ct.budget_status()
        assert st.budget_usd == 1.0
        assert st.spent_usd > 0
        assert not st.exceeded


def test_cost_tracker_dashboard():
    from agent.cost_tracker import CostTracker
    from agent.token_counter import TokenCounter
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        counter = TokenCounter(persist_path=Path(td) / "u.json")
        ct = CostTracker(counter=counter, budget_usd=5.0)
        dash = ct.dashboard()
        assert "Cost Dashboard" in dash


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
def test_telemetry_disabled_by_default():
    from agent.telemetry import TelemetryCollector
    tc = TelemetryCollector()
    assert not tc.enabled
    tc.record("test")  # should be a no-op
    assert len(tc._events) == 0


def test_telemetry_enable_and_record():
    from agent.telemetry import TelemetryCollector
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tc = TelemetryCollector(persist_path=Path(td) / "tel.jsonl")
        tc.enable()
        tc.record("turn", duration_s=1.0, success=True)
        tc.record("turn", duration_s=0.5, success=False)
        s = tc.summary()
        assert s["enabled"] is True
        assert s["total_events"] == 2
        assert s["success_count"] == 1


def test_telemetry_dashboard():
    from agent.telemetry import TelemetryCollector
    tc = TelemetryCollector()
    dash = tc.dashboard()
    assert "OFF" in dash


# ---------------------------------------------------------------------------
# Profiler
# ---------------------------------------------------------------------------
def test_profiler_disabled_by_default():
    from agent.profiler import Profiler
    p = Profiler()
    assert not p.enabled


def test_profiler_record():
    from agent.profiler import Profiler
    p = Profiler()
    p.enable()
    p.record("llm_call", 1.5)
    p.record("tool_exec", 0.5)
    summary = p.aggregate_summary()
    assert "llm_call" in summary
    assert "tool_exec" in summary


def test_profiler_time_decorator():
    from agent.profiler import Profiler
    p = Profiler()
    p.enable()

    @p.time("test_op")
    def add(a, b):
        return a + b

    assert add(2, 3) == 5
    summary = p.aggregate_summary()
    assert "test_op" in summary


def test_profiler_toggle():
    from agent.profiler import Profiler
    p = Profiler()
    assert p.toggle() is True
    assert p.enabled is True
    assert p.toggle() is False
    assert p.enabled is False


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------
def test_recovery_save_and_load(tmp_path):
    from agent.recovery import RecoveryManager, Checkpoint
    rm = RecoveryManager(session_id="test-session")
    rm.persist_path = tmp_path / "recovery"
    rm.persist_path.mkdir(parents=True, exist_ok=True)
    cp = Checkpoint(
        session_id="test-session",
        provider="zen",
        model="mimo-v2.5-free",
        messages=[{"role": "user", "content": "hi"}],
        turn_count=1,
    )
    path = rm.save(cp)
    assert path.exists()


def test_recovery_dashboard_empty(tmp_path):
    from agent.recovery import RecoveryManager
    rm = RecoveryManager()
    rm.persist_path = tmp_path / "recovery"
    rm.persist_path.mkdir(parents=True, exist_ok=True)
    dash = rm.dashboard()
    assert "No recovery" in dash or "checkpoints" in dash.lower()


# ---------------------------------------------------------------------------
# Goal history
# ---------------------------------------------------------------------------
def test_goal_history_save_and_load(tmp_path, monkeypatch):
    from agent.goal_history import GoalHistory, GoalRecord, GOALS_DIR
    history = GoalHistory()
    monkeypatch.setattr(history, "_path", lambda gid: tmp_path / f"{gid}.json")
    record = GoalRecord(goal="test goal", effort="ultrahype", status="complete")
    history.save(record)
    loaded = history.load(record.id)
    assert loaded is not None
    assert loaded.goal == "test goal"


def test_goal_history_dashboard(tmp_path, monkeypatch):
    from agent.goal_history import GoalHistory, GoalRecord
    history = GoalHistory()
    monkeypatch.setattr(history, "_path", lambda gid: tmp_path / f"{gid}.json")
    monkeypatch.setattr("agent.goal_history.GOALS_DIR", tmp_path)
    record = GoalRecord(goal="test goal", status="complete", rounds=3)
    history.save(record)
    dash = history.dashboard()
    assert "Goal history" in dash or "test goal" in dash


# ---------------------------------------------------------------------------
# Prompt library
# ---------------------------------------------------------------------------
def test_prompt_library_list():
    from agent.prompt_library import list_templates
    templates = list_templates()
    assert len(templates) >= 10
    assert any(t.name == "senior-engineer" for t in templates)


def test_prompt_library_get():
    from agent.prompt_library import get_template
    t = get_template("senior-engineer")
    assert t is not None
    assert t.category == "coding"


def test_prompt_library_categories():
    from agent.prompt_library import categories
    cats = categories()
    assert "coding" in cats
    assert "security" in cats


def test_prompt_library_render():
    from agent.prompt_library import get_template, render
    t = get_template("senior-engineer")
    text = render(t)
    assert "senior principal" in text


# ---------------------------------------------------------------------------
# Goal templates
# ---------------------------------------------------------------------------
def test_goal_templates_list():
    from agent.goal_templates import list_templates
    templates = list_templates()
    assert len(templates) >= 10
    assert any(t.name == "ship-feature" for t in templates)


def test_goal_templates_get():
    from agent.goal_templates import get_template
    t = get_template("fix-bug")
    assert t is not None
    assert t.category == "coding"


def test_goal_templates_render():
    from agent.goal_templates import get_template, render
    t = get_template("ship-feature")
    text = render(t, feature="a new REST API")
    assert "a new REST API" in text


def test_goal_templates_categories():
    from agent.goal_templates import categories
    cats = categories()
    assert "coding" in cats


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
def test_render_token_widget():
    from agent.widgets import render_token_widget
    snap = {"session_total": 1000, "session_cost_fmt": "$0.01", "goal_turns": 0, "goal_total": 0, "goal_cost_fmt": "free", "all_time_total": 5000}
    text = render_token_widget(snap)
    assert "1,000" in text.plain


def test_render_status_bar():
    from agent.widgets import render_status_bar
    snap = {"session_total": 100, "session_cost_fmt": "$0.01", "goal_turns": 0, "goal_total": 0, "goal_cost_fmt": "free", "all_time_total": 500}
    bar = render_status_bar("zen", "mimo-v2.5-free", snap, theme_name="neon")
    assert "zen" in bar.plain
    assert "mimo-v2.5-free" in bar.plain


def test_render_diff():
    from agent.widgets import render_diff
    panel = render_diff("hello\n", "world\n")
    assert "diff" in panel.title


def test_notification_system():
    from agent.widgets import NotificationSystem
    ns = NotificationSystem()
    ns.info("test message")
    pending = ns.pending()
    assert len(pending) == 1
    assert pending[0].message == "test message"


def test_command_palette_search():
    from agent.widgets import CommandPalette
    palette = CommandPalette([("/help", "show help"), ("/cost", "show cost"), ("/exit", "quit")])
    results = palette.search("cost")
    assert len(results) == 1
    assert results[0][0] == "/cost"


def test_command_palette_fuzzy():
    from agent.widgets import CommandPalette
    palette = CommandPalette([("/help", "show help"), ("/cost", "show cost")])
    results = palette.search("hp")
    assert any(r[0] == "/help" for r in results)


# ---------------------------------------------------------------------------
# New tool groups
# ---------------------------------------------------------------------------
def test_code_analysis_analyze_structure(tmp_path):
    from agent.tools.code_analysis_tools import analyze_structure
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    pass\n\nclass Bar:\n    def method(self):\n        pass\n", encoding="utf-8")
    res = analyze_structure(str(f))
    assert "foo" in res.output
    assert "Bar" in res.output


def test_code_analysis_complexity(tmp_path):
    from agent.tools.code_analysis_tools import count_complexity
    f = tmp_path / "mod.py"
    f.write_text("def foo(x):\n    if x:\n        for i in range(x):\n            if i > 5:\n                print(i)\n", encoding="utf-8")
    res = count_complexity(str(f))
    assert "foo" in res.output


def test_code_analysis_find_todos(tmp_path):
    from agent.tools.code_analysis_tools import find_todos
    f = tmp_path / "mod.py"
    f.write_text("# TODO: implement this\ndef foo():\n    # FIXME: bug here\n    pass\n", encoding="utf-8")
    res = find_todos(str(f))
    assert res.metadata["count"] >= 2


def test_code_analysis_list_imports(tmp_path):
    from agent.tools.code_analysis_tools import list_imports
    f = tmp_path / "mod.py"
    f.write_text("import os\nfrom pathlib import Path\n", encoding="utf-8")
    res = list_imports(str(f))
    assert "os" in res.output
    assert "pathlib" in res.output


def test_security_scan_secrets(tmp_path):
    from agent.tools.security_tools import scan_secrets
    f = tmp_path / "config.py"
    f.write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    res = scan_secrets(str(f))
    assert res.metadata["count"] >= 1


def test_security_scan_vulns(tmp_path):
    from agent.tools.security_tools import scan_vulns
    f = tmp_path / "mod.py"
    f.write_text("import os\nos.system('ls')\nresult = eval('1+1')\n", encoding="utf-8")
    res = scan_vulns(str(f))
    assert res.metadata["count"] >= 2


def test_security_scan_deps(tmp_path):
    from agent.tools.security_tools import scan_deps
    f = tmp_path / "requirements.txt"
    f.write_text("requests==1.0.0\nurllib3==1.0\n", encoding="utf-8")
    res = scan_deps(str(f))
    assert "requests" in res.output


def test_scaffold_python_project(tmp_path):
    from agent.tools.scaffold_tools import scaffold_python_project
    res = scaffold_python_project("myproj", base_dir=str(tmp_path))
    assert res.success
    assert (tmp_path / "myproj" / "pyproject.toml").exists()
    assert (tmp_path / "myproj" / "README.md").exists()
    assert (tmp_path / "myproj" / "tests" / "test_main.py").exists()


def test_scaffold_node_project(tmp_path):
    from agent.tools.scaffold_tools import scaffold_node_project
    res = scaffold_node_project("myapp", base_dir=str(tmp_path))
    assert res.success
    assert (tmp_path / "myapp" / "package.json").exists()
    assert (tmp_path / "myapp" / "index.js").exists()


def test_scaffold_web_project(tmp_path):
    from agent.tools.scaffold_tools import scaffold_web_project
    res = scaffold_web_project("mysite", base_dir=str(tmp_path))
    assert res.success
    assert (tmp_path / "mysite" / "index.html").exists()
    assert (tmp_path / "mysite" / "css" / "style.css").exists()


def test_scaffold_existing_dir_fails(tmp_path):
    from agent.tools.scaffold_tools import scaffold_python_project
    (tmp_path / "exists").mkdir()
    res = scaffold_python_project("exists", base_dir=str(tmp_path))
    assert not res.success


def test_docgen_extract_docstrings(tmp_path):
    from agent.tools.docgen_tools import extract_docstrings
    f = tmp_path / "mod.py"
    f.write_text('"""Module doc."""\n\ndef foo():\n    """Foo doc."""\n    pass\n\ndef bar():\n    pass\n', encoding="utf-8")
    res = extract_docstrings(str(f))
    assert "Module doc" in res.output
    assert "Foo doc" in res.output
    assert "no docstring" in res.output  # bar has none


def test_docgen_generate_stubs(tmp_path):
    from agent.tools.docgen_tools import generate_doc_stubs
    f = tmp_path / "mod.py"
    f.write_text("def foo():\n    pass\n\ndef bar():\n    pass\n", encoding="utf-8")
    res = generate_doc_stubs(str(f))
    assert res.metadata["count"] == 2


def test_docgen_count_test_coverage(tmp_path):
    from agent.tools.docgen_tools import count_test_coverage
    src = tmp_path / "mod.py"
    src.write_text("def foo():\n    pass\ndef bar():\n    pass\n", encoding="utf-8")
    tst = tmp_path / "test_mod.py"
    tst.write_text("def test_foo():\n    assert True\n", encoding="utf-8")
    res = count_test_coverage(str(src), str(tst))
    assert res.metadata["source_functions"] == 2
    assert res.metadata["test_functions"] == 1


def test_docgen_list_test_functions(tmp_path):
    from agent.tools.docgen_tools import list_test_functions
    f = tmp_path / "test_mod.py"
    f.write_text("def test_foo():\n    pass\ndef helper():\n    pass\ndef test_bar():\n    pass\n", encoding="utf-8")
    res = list_test_functions(str(f))
    assert res.metadata["count"] == 2


# ---------------------------------------------------------------------------
# Enterprise commands registration
# ---------------------------------------------------------------------------
def test_enterprise_commands_built():
    from agent.commands.enterprise_commands import build_enterprise_commands
    cmds = build_enterprise_commands()
    assert len(cmds) >= 40
    names = [c.name for c in cmds]
    assert "/cost" in names
    assert "/dashboard" in names
    assert "/goal-resume" in names
    assert "/consensus" in names


def test_full_registry_has_enterprise_commands():
    from agent.commands import build_command_registry
    reg = build_command_registry()
    names = [c.name for c in reg.all()]
    assert "/cost" in names
    assert "/branch" in names
    assert "/telemetry" in names
    assert "/recover" in names
    assert "/dashboard" in names


def test_full_registry_has_new_tools():
    from agent.tools import build_default_registry
    reg = build_default_registry()
    names = [t.name for t in reg.all()]
    assert "analyze_structure" in names
    assert "scan_secrets" in names
    assert "scaffold_python_project" in names
    assert "extract_docstrings" in names
