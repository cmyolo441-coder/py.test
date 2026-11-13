"""Regression tests: subprocess hygiene + Popen tracking (bg-fix round)."""
import os
import subprocess
import sys
import threading
import time

import pytest

from fullagent import cancelguard, hooks


def test_track_popen_parallel_never_leaves_popen_wrapped():
    real = subprocess.Popen

    def call():
        with cancelguard.track_popen(thread_only=True) as spawned:
            time.sleep(0.002)
            subprocess.run(["true"])
            assert len(spawned) == 1

    for _ in range(25):
        ts = [threading.Thread(target=call) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    # restored exactly; no wrapper chain accumulates across batches
    assert subprocess.Popen is real


def test_track_popen_thread_only_isolates_parallel_calls():
    counts = {}
    barrier = threading.Barrier(3)

    def call(i):
        with cancelguard.track_popen(thread_only=True) as spawned:
            barrier.wait()
            subprocess.run(["true"])
            barrier.wait()
            counts[i] = len(spawned)

    with cancelguard.track_popen() as everything:
        ts = [threading.Thread(target=call, args=(i,)) for i in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert counts == {0: 1, 1: 1, 2: 1}
    assert len(everything) == 3  # global tracker still sees all threads


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_run_in_group_timeout_kills_grandchildren(tmp_path):
    marker = tmp_path / "survived"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        cancelguard.run_in_group(
            f"(sleep 1; echo alive > {marker}) & sleep 30",
            shell=True, timeout=0.3)
    assert time.monotonic() - t0 < 5
    time.sleep(1.3)
    assert not marker.exists(), "grandchild outlived the timeout"


def test_kill_process_group_never_targets_own_group():
    # a child in OUR process group: killpg(getpgid(child)) would kill us
    proc = subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(30)"])
    try:
        cancelguard.kill_process_group(proc, wait=5)
        assert proc.poll() is not None  # the child died, we did not
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_pre_tool_hook_still_blocks_huge_args(tmp_path):
    """>128 KiB args used to make the hook fail to spawn (E2BIG) and the
    fail-open path then ALLOWED the call — a safety-hook bypass."""
    cfg = tmp_path / "hooks.json"
    cfg.write_text('{"PreToolUse": [{"match": "write_file", '
                   '"command": "echo nope >&2; exit 1"}]}')
    allowed, _, msg = hooks.run_pre_tool(
        "write_file", {"path": "x", "content": "A" * 300_000}, path=cfg)
    assert allowed is False
    assert "nope" in msg


def test_hook_gets_full_args_via_file_and_cleans_up(tmp_path):
    cfg = tmp_path / "hooks.json"
    out = tmp_path / "seen"
    cfg.write_text('{"PreToolUse": [{"match": ".*", "command": '
                   f'"wc -c < \\"$HOOK_ARGS_FILE\\" > {out}; '
                   f'echo $HOOK_ARGS_FILE >> {out}"}}]}}')
    allowed, _, _ = hooks.run_pre_tool(
        "write_file", {"content": "B" * 200_000}, path=cfg)
    assert allowed
    size, path = out.read_text().split()
    assert int(size) > 200_000
    assert not os.path.exists(path)  # temp spill file removed
