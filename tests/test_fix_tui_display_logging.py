"""Bug: a logging WARNING ("16:11:05 │ WARNING │ fullagent.client │ token
pre-check: …") was printed INSIDE the prompt box.

FullAgent's logger wrote to stderr, which prompt_toolkit's patch_stdout
does not proxy, so any record emitted while the TUI ran was drawn over
the input box. While UI.run() owns the terminal every record — fullagent
and root loggers, worker threads, loggers created later, Python
warnings — must go to <APP_DIR>/logs/fullagent.log instead; headless use
keeps logging to stderr.
"""
import io
import logging
import os
import sys
import tempfile
import threading
import unittest
import warnings
from pathlib import Path
from unittest import mock

os.environ.setdefault("FULLAGENT_HOME",
                      tempfile.mkdtemp(prefix="fix_tui_log_"))

from fullagent import _foundation, config  # noqa: E402
from fullagent._foundation import capture_logs_to_file, get_logger  # noqa: E402,E501

from test_swarm_dash_tui import FakeManager, run_with_ui  # noqa: E402


def _console_handler():
    get_logger("client")
    fa = logging.getLogger("fullagent")
    return next(h for h in fa.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler))


class _StderrTrap:
    """Point every console path (the fullagent stderr handler, sys.stderr
    for lastResort / warnings) at one buffer."""

    def __enter__(self):
        self.buf = io.StringIO()
        self.handler = _console_handler()
        self.old_stream = self.handler.setStream(self.buf)
        self.old_stderr = sys.stderr
        sys.stderr = self.buf
        return self.buf

    def __exit__(self, *exc):
        sys.stderr = self.old_stderr
        self.handler.setStream(self.old_stream)


def _emit_everything(tag):
    get_logger("client").warning("token pre-check: %s main", tag)
    t = threading.Thread(
        target=lambda: get_logger("subagents").error("%s worker", tag))
    t.start()
    t.join()
    # a logger created only now, inside the run
    logging.getLogger(f"fullagent.brandnew_{tag}").warning("%s late", tag)
    logging.getLogger(f"thirdparty_{tag}").warning("%s root", tag)
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        warnings.warn(f"{tag} pywarning")


class TuiRunRoutesLogsToFile(unittest.TestCase):
    def test_no_record_reaches_stderr_while_tui_runs(self):
        home = Path(tempfile.mkdtemp(prefix="fix_tui_log_home_"))

        def body(ui):
            with _StderrTrap() as buf, \
                    mock.patch.object(config, "APP_DIR", home):
                ui.app.run = lambda *a, **k: _emit_everything("intui")
                ui.run()
                during = buf.getvalue()
                # after the TUI exits, headless behaviour is back
                get_logger("client").warning("after tui")
                after = buf.getvalue()
            self.assertEqual(during, "",
                             "log output reached stderr inside the TUI")
            self.assertIn("after tui", after)
            log = (home / "logs" / "fullagent.log").read_text("utf-8")
            for needle in ("token pre-check: intui main", "intui worker",
                           "intui late", "intui root", "intui pywarning",
                           "fullagent.client"):
                self.assertIn(needle, log)
            self.assertEqual(ui.log_path, home / "logs" / "fullagent.log")
        run_with_ui(body, FakeManager(0))


class CaptureContextManager(unittest.TestCase):
    def test_restores_handlers_and_last_resort(self):
        root = logging.getLogger()
        before_root = list(root.handlers)
        before_fa = list(logging.getLogger("fullagent").handlers)
        before_last = logging.lastResort
        path = Path(tempfile.mkdtemp()) / "x" / "a.log"
        with capture_logs_to_file(path):
            self.assertIsNotNone(_foundation._capture_sink)
        self.assertIsNone(_foundation._capture_sink)
        self.assertEqual(root.handlers, before_root)
        self.assertEqual(logging.getLogger("fullagent").handlers, before_fa)
        self.assertIs(logging.lastResort, before_last)

    def test_console_handlers_on_other_loggers_are_detached(self):
        lg = logging.getLogger("thirdparty_noisy")
        lg.propagate = False
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        lg.addHandler(h)
        try:
            with mock.patch.object(sys, "stderr", buf):
                h.setStream(sys.stderr)
                path = Path(tempfile.mkdtemp()) / "b.log"
                with capture_logs_to_file(path):
                    lg.warning("noisy inside")
                self.assertEqual(buf.getvalue(), "")
                self.assertIn(h, lg.handlers)
        finally:
            lg.removeHandler(h)

    def test_unwritable_path_drops_records_instead_of_printing(self):
        with _StderrTrap() as buf:
            with capture_logs_to_file("/proc/definitely/not/here.log"):
                get_logger("client").warning("nowhere")
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
