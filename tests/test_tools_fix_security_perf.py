"""Regression tests for tool-execution / security / long-session fixes."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from fullagent import (dbtools, editbackup, filestat, images, readguard,
                       tooldedup, tools, webfetch)
from fullagent.fuzzymatch import find_best_match
from fullagent.multiedit import multi_edit


class _Agent:
    pass


class ToolDedupTests(unittest.TestCase):
    def test_shell_results_are_never_replayed(self):
        a = _Agent()
        tooldedup.store(a, "run_command", {"command": "pytest"}, "1 failed")
        self.assertIsNone(tooldedup.check(a, "run_command",
                                          {"command": "pytest"}))
        tooldedup.store(a, "BashOutput", {"task_id": "x"}, "(no new output)")
        self.assertIsNone(tooldedup.check(a, "BashOutput", {"task_id": "x"}))

    def test_mutation_invalidates_cached_reads(self):
        a = _Agent()
        tooldedup.store(a, "read_file", {"path": "f"}, "old content")
        self.assertIsNotNone(tooldedup.check(a, "read_file", {"path": "f"}))
        tooldedup.store(a, "edit_file", {"path": "f"}, "Updated f", "done")
        self.assertIsNone(tooldedup.check(a, "read_file", {"path": "f"}))

    def test_failed_edit_still_deduped(self):
        a = _Agent()
        tooldedup.store(a, "MultiEdit", {"x": 1}, "ERROR: miss", "error")
        hit = tooldedup.check(a, "MultiEdit", {"x": 1})
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "error")


@unittest.skipUnless(os.name == "posix", "POSIX process groups")
class ShellTests(unittest.TestCase):
    def test_background_child_does_not_hang_command(self):
        t = time.monotonic()
        out = tools.run_command("sleep 20 & echo started", timeout=15)
        self.assertLess(time.monotonic() - t, 5)
        self.assertIn("exit code: 0", out)
        self.assertIn("started", out)

    def test_huge_output_is_bounded_and_fast(self):
        t = time.monotonic()
        out = tools.run_command(
            "yes 0123456789 | head -c 50000000; echo DONE", timeout=60)
        self.assertLess(time.monotonic() - t, 20)
        self.assertLessEqual(len(out), 30_000)
        self.assertIn("DONE", out)

    def test_timeout_keeps_partial_output(self):
        out = tools.run_command("echo partial-marker; sleep 30", timeout=1)
        self.assertTrue(out.startswith("ERROR: command timed out"), out)
        self.assertIn("partial-marker", out)

    def test_command_reading_stdin_does_not_block(self):
        t = time.monotonic()
        out = tools.run_command("cat; echo after-cat", timeout=10)
        self.assertLess(time.monotonic() - t, 5)
        self.assertIn("after-cat", out)

    def test_streaming_lines_and_crlf(self):
        got = []
        tools.run_command("printf 'a\\r\\nb\\nc'", on_output=lambda l, t:
                          got.append(l))
        self.assertEqual(got, ["a", "b", "c"])

    def test_kill_tree_never_kills_own_process_group(self):
        # Run in a child interpreter: if the guard regresses, killpg would
        # SIGKILL that interpreter (exit -9), not the test runner.
        code = textwrap.dedent("""
            import subprocess, sys
            from fullagent import tools
            p = subprocess.Popen([sys.executable, '-c',
                                  'import time; time.sleep(30)'])
            tools._kill_process_tree(p)
            print('alive', p.returncode)
        """)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60,
                           cwd=str(Path(tools.__file__).parent.parent))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alive -9", r.stdout)

    def test_run_capture_timeout_kills_group_without_hanging(self):
        t = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            # the grandchild keeps the pipes open; plain subprocess.run
            # would wait for it forever after killing bash
            tools._run_capture(["bash", "-c", "sleep 30 & sleep 30"], 1)
        self.assertLess(time.monotonic() - t, 10)


class WebFetchTests(unittest.TestCase):
    def test_redirect_hop_to_internal_host_is_blocked_before_request(self):
        h = webfetch._ValidatingRedirectHandler()
        req = urllib.request.Request("http://example.com/")
        with self.assertRaises(Exception) as cm:
            h.redirect_request(req, None, 302, "Found", {},
                               "http://169.254.169.254/latest/meta-data")
        self.assertIn("redirect blocked", str(cm.exception))

    def test_embedded_ipv4_in_ipv6_is_blocked(self):
        self.assertTrue(webfetch._host_error("::ffff:127.0.0.1"))
        self.assertTrue(webfetch._host_error("64:ff9b::7f00:1"))


class DbToolsTests(unittest.TestCase):
    def test_runaway_query_is_aborted(self):
        with TemporaryDirectory() as d:
            db = os.path.join(d, "a.db")
            c = sqlite3.connect(db)
            c.execute("create table t(x)")
            c.executemany("insert into t values(?)",
                          [(i,) for i in range(1500)])
            c.commit()
            c.close()
            old = dbtools.QUERY_TIME_LIMIT
            dbtools.QUERY_TIME_LIMIT = 0.5
            try:
                t = time.monotonic()
                out = dbtools.db_query(
                    db, "select count(*) from t a, t b, t c")
            finally:
                dbtools.QUERY_TIME_LIMIT = old
            self.assertLess(time.monotonic() - t, 10)
            self.assertIn("time limit", out)


class EditToolTests(unittest.TestCase):
    def test_external_change_warns_even_with_readguard(self):
        with TemporaryDirectory() as d:
            agent = _Agent()
            agent.tools = dict(tools.build_registry())
            readguard.register(agent)
            f = Path(d) / "x.py"
            f.write_text("a = 1\nb = 2\n")
            agent.tools["read_file"].handler(str(f))
            agent.readguard_seen.clear()  # next turn
            time.sleep(0.01)
            f.write_text("a = 10\nb = 3\n")  # external edit
            out = agent.tools["edit_file"].handler(str(f), "a = 1\nb = 2",
                                                   "z")
            self.assertIn("changed since you read it", out)
            self.assertEqual(f.read_text(), "a = 10\nb = 3\n")

    def test_line_anchor_disambiguates_without_eating_indentation(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "m.txt"
            f.write_text("def a():\n    x = 1\n\ndef b():\n    x = 1\n")
            out = multi_edit(str(f), [{"old_string": "x = 1",
                                       "new_string": "x = 2",
                                       "start_line": 5}])
            self.assertFalse(out.startswith("ERROR"), out)
            self.assertEqual(f.read_text(),
                             "def a():\n    x = 1\n\ndef b():\n    x = 2\n")

    def test_crlf_files_stay_crlf(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "w.txt"
            f.write_bytes(b"a\r\nb\r\nc\r\n")
            self.assertTrue(tools.edit_file(str(f), "a\nb", "A\nB")
                            .startswith("Updated"))
            self.assertEqual(f.read_bytes(), b"A\r\nB\r\nc\r\n")
            out = tools.apply_patch(
                f"--- a/{f}\n+++ b/{f}\n@@ -1,3 +1,3 @@\n A\n-B\n+X\n c\n")
            self.assertTrue(out.startswith("Updated"), out)
            self.assertEqual(f.read_bytes(), b"A\r\nX\r\nc\r\n")
            out = multi_edit(str(f), [{"old_string": "X\nc",
                                       "new_string": "Y\nZ"}])
            self.assertFalse(out.startswith("ERROR"), out)
            self.assertEqual(f.read_bytes(), b"A\r\nY\r\nZ\r\n")

    def test_apply_patch_is_all_or_nothing(self):
        with TemporaryDirectory() as d:
            f1, f2 = Path(d) / "one.txt", Path(d) / "two.txt"
            f1.write_text("a\nb\n")
            f2.write_text("c\nd\n")
            patch = (f"--- a/{f1}\n+++ b/{f1}\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
                     f"--- a/{f2}\n+++ b/{f2}\n@@ -1,2 +1,2 @@\n WRONG\n-d\n+D\n")
            out = tools.apply_patch(patch)
            self.assertTrue(out.startswith("ERROR"), out)
            self.assertEqual(f1.read_text(), "a\nb\n")  # untouched

    def test_read_file_streams_large_files(self):
        with TemporaryDirectory() as d:
            f = Path(d) / "big.log"
            with open(f, "w") as fh:
                for i in range(200_000):
                    fh.write(f"row {i}\n")
            self.assertGreater(f.stat().st_size, 1_000_000)
            # force the streaming path regardless of the threshold
            old = tools._READ_WHOLE_MAX_BYTES
            tools._READ_WHOLE_MAX_BYTES = 1000
            try:
                out = tools.read_file(str(f), offset=199_999, limit=5)
            finally:
                tools._READ_WHOLE_MAX_BYTES = old
            self.assertIn("200000 lines total", out)
            self.assertIn("row 199999", out)
            self.assertNotIn("ERROR", out)

    def test_fuzzymatch_ambiguous_exact_not_confident(self):
        s, e, sugg = find_best_match("a=1\nb\na=1\n", "a=1")
        self.assertIsNone(s)
        self.assertEqual(len(sugg), 2)

    def test_fuzzy_suggestions_fast_on_big_files(self):
        content = "\n".join(f"    value_{i} = compute(arg{i})"
                            for i in range(5000))
        needle = "\n".join(f"    thing_{i} = nothing({i})" for i in range(40))
        t = time.monotonic()
        find_best_match(content, needle)
        self.assertLess(time.monotonic() - t, 3)


class EditBackupTests(unittest.TestCase):
    def test_rollback_ignores_other_files_sharing_a_prefix(self):
        with TemporaryDirectory() as d:
            old_base, old_cwd = editbackup.SNAPSHOT_BASE, os.getcwd()
            editbackup.SNAPSHOT_BASE = os.path.join(d, "snaps")
            os.chdir(d)
            try:
                Path("a.py").write_text("A")
                Path("a.py.orig").write_text("ORIG")
                editbackup.snapshot_file("a.py", "s")
                editbackup.snapshot_file("a.py.orig", "s")
                Path("a.py").write_text("B")
                out = editbackup.rollback("a.py", "s")
                self.assertTrue(out.startswith("rolled back"), out)
                self.assertEqual(Path("a.py").read_text(), "A")
            finally:
                os.chdir(old_cwd)
                editbackup.SNAPSHOT_BASE = old_base

    def test_edit_rollback_needs_approval(self):
        self.assertEqual(editbackup.make_edit_rollback_tool().risk,
                         tools.RISK_CONFIRM)


class ImageTests(unittest.TestCase):
    def test_large_image_not_inlined_as_text(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "big.png"
            p.write_bytes(images._PNG_SIG + b"\x00" * 200_000)
            out = images.describe_image(str(p))
            self.assertLess(len(out), 2000)
            self.assertIn("omitted", out)


if __name__ == "__main__":
    unittest.main()
