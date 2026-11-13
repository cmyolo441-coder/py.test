import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import ctxmeter, slashcmds  # noqa: E402
from fullagent.enforcex import policy  # noqa: E402


def test_policy_catch_all_rule_applies():
    pol = policy.Policy(policy.PolicySet.from_dict({
        "default_mode": "warn",
        "rules": [{"rule_id": "*", "mode": "block"},
                  {"rule_id": "R1", "mode": "log"}]}))
    assert pol.decide({"rule_id": "R9"}).action == "block"
    # more specific rule still wins over the catch-all
    assert pol.decide({"rule_id": "R1"}).action == "log"


def test_policy_catch_all_respects_stage_gating():
    pol = policy.Policy(policy.PolicySet.from_dict({
        "default_mode": "warn",
        "rules": [{"rule_id": "*", "mode": "block", "not_stages": ["audit"]}]}))
    assert pol.decide({"rule_id": "X", "stage": "audit"}).action == "warn"
    assert pol.decide({"rule_id": "X", "stage": "final"}).action == "block"


def test_ctxmeter_infinite_width_does_not_raise():
    bar, _style = ctxmeter.render_meter(50, width=float("inf"))[0]
    assert len(bar) == ctxmeter.DEFAULT_WIDTH


def test_slashcmds_bom_frontmatter(tmp_path):
    (tmp_path / "rev.md").write_bytes(
        "﻿---\ndescription: Review it\n---\nReview $ARGUMENTS\n"
        .encode("utf-8"))
    cmds = slashcmds.load_commands(str(tmp_path))
    assert cmds["rev"]["description"] == "Review it"
    assert slashcmds.expand("rev", "a.py", str(tmp_path)) == "Review a.py"
