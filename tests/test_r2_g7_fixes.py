import os
import subprocess
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

import json
import shutil

import pytest

from fullagent import fuzz, gitops, hooks, smartctx, turnresume
from fullagent.enforcex import consensus, sandbox


def test_hook_undecodable_output_still_blocks(tmp_path):
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"PreToolUse": [
        {"match": ".*", "command": "printf 'bad \\377 byte' >&2; exit 1"}]}))
    allowed, _, msg = hooks.run_pre_tool("x", {}, path=str(cfg))
    assert allowed is False
    assert "bad" in msg


def test_sandbox_non_dict_capabilities_map_does_not_raise():
    sb = sandbox.Sandbox([lambda response: True],
                         capabilities_map=["read_response"])
    assert sb.evaluate("x").all_passed is True


def test_consensus_string_abstain_is_not_dissent():
    g = consensus.ConsensusGate(voters=[
        consensus.Voter("a", 1, lambda t, r, s: "allow"),
        consensus.Voter("b", 1, lambda t, r, s: "abstain")])
    r = g.evaluate("x")
    assert r.final_action == "allow"
    assert r.dissent == []


def test_turnresume_snapshot_keeps_none_content():
    snap = turnresume._snapshot_messages(
        [{"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}])
    assert snap[0]["content"] is None


def test_fuzz_shrink_does_not_drift_from_raise_to_hang():
    import threading
    ev = threading.Event()

    def target(s):
        if isinstance(s, str) and s == "":
            ev.wait(5)  # hangs on the simpler input
            return None
        raise TimeoutError("boom")

    old = fuzz.SHRINK_PROBE_TIMEOUT
    crash = fuzz.Crash(args=("abc",), error="TimeoutError: boom")
    fz = fuzz.Fuzzer.__new__(fuzz.Fuzzer)
    fz.gen = fuzz.Generator(0)
    fz.log = type("L", (), {"append": lambda *a, **k: None})()
    try:
        fz._shrink(crash, target, 0.2)
    finally:
        ev.set()
        fuzz.SHRINK_PROBE_TIMEOUT = old
    assert crash.shrunk_args != ("",)
    assert crash.shrunk_error.startswith("TimeoutError: boom")


@pytest.mark.skipif(shutil.which("git") is None, reason="git required")
def test_gitops_status_keeps_leading_space_and_empty_log(tmp_path):
    d = str(tmp_path)
    assert gitops.git_log(5, cwd=d).startswith("ERROR")  # not a repo yet
    subprocess.run(["git", "init", "-q", d], check=True)
    assert gitops.git_log(5, cwd=d) == "(no commits yet)"
    subprocess.run(["git", "-C", d, "config", "user.email", "t@e.x"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    (tmp_path / "a.txt").write_text("a\n")
    assert gitops.git_commit("init", cwd=d).startswith("committed:")
    (tmp_path / "a.txt").write_text("b\n")
    st = gitops.git_status(cwd=d)
    assert " M a.txt" in st.splitlines()


def test_smartctx_mentions(tmp_path):
    (tmp_path / "notes.txt").write_text("hi\n")
    cwd = str(tmp_path)
    assert smartctx.extract_mentions("mail bob@example.com", cwd) == []
    assert smartctx.extract_mentions("read @notes.txt.", cwd) == ["notes.txt"]
    assert "[skipped: unreadable]" in smartctx.read_attachments(
        ["/dev/zero"], cwd)
