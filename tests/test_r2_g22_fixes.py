"""Regression tests for round-2 group-22 audit fixes."""
import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from types import SimpleNamespace  # noqa: E402

from fullagent import planmode  # noqa: E402
from fullagent._foundation import capture_logs_to_file, get_logger  # noqa: E402
from fullagent.tools import RISK_CONFIRM  # noqa: E402


# ── detect ───────────────────────────────────────────────────────────────







# ── forge ────────────────────────────────────────────────────────────────







# ── _foundation ──────────────────────────────────────────────────────────

def test_capture_logs_writes_each_record_once(tmp_path):
    log = get_logger("r2g22")
    path = tmp_path / "cap.log"
    with capture_logs_to_file(path):
        log.warning("only-once-marker")
    assert path.read_text(encoding="utf-8").count("only-once-marker") == 1


# ── planmode ─────────────────────────────────────────────────────────────

def test_revised_plan_requires_fresh_approval():
    agent = SimpleNamespace(tools={})
    planmode.register(agent)
    try:
        planmode.enter_plan_mode(agent)
        pw = agent.tools["PlanWrite"].handler
        pw(plan="1. read")
        planmode.approve_plan(agent)
        assert not planmode.should_block_mutating_tool(
            "Write", RISK_CONFIRM, agent)[0]
        pw(plan="1. delete everything")
        assert planmode.plan_requires_approval(agent)
        assert planmode.should_block_mutating_tool(
            "Write", RISK_CONFIRM, agent)[0]
    finally:
        planmode.exit_plan_mode(agent)
