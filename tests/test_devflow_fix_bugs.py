"""Regression tests for the devflow-area fixes (review / prreview / ci / cov /
bandit / attest / doctor / codeinit / export / backup / report / jsonout /
streamlog / cassette / termsetup / voicein / merge).

All offline and fast; app state is isolated under a temp FULLAGENT_HOME.
"""

import io
import json
import math
import os
import sys
import tarfile
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

_tmp_home = tempfile.mkdtemp(prefix="devflow_home_")
os.environ.setdefault("FULLAGENT_HOME", _tmp_home)

_REPO = str(Path(__file__).resolve().parent.parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest  # noqa: E402

from fullagent import (attest, backup, bandit, cassette, ci, codeinit,  # noqa: E402
                       cov, doctor, export, jsonout, merge, prreview,
                       report, review, streamlog, termsetup, voicein)
from fullagent.kernel import EventLog  # noqa: E402


# --------------------------------------------------------------------------
# review / prreview: diff parsing
# --------------------------------------------------------------------------

_FF_DIFF = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n"
            "@@ -1,1 +1,3 @@\n ctx\n+a = 1\x0c\n+except:\n")


def test_review_formfeed_does_not_shift_line_numbers():
    found = review.review_diff(_FF_DIFF)
    bare = [f for f in found if "Bare" in f["message"]]
    assert bare and bare[0]["line"] == 3, found


def test_prreview_formfeed_and_crlf_parse():
    files = prreview.parse_diff(_FF_DIFF.replace("\n", "\r\n"))
    assert files[0]["added"] == [(2, "a = 1\x0c"), (3, "except:")]
    assert files[0]["added_count"] == 2


def test_prreview_secret_not_echoed_in_findings():
    d = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n"
         '+API_KEY = "abcdefghijklmnopqrstuvwxyz"\n')
    crit = [f for f in prreview.quick_heuristics(d)
            if f["severity"] == "critical"]
    assert crit
    assert "abcdefghijklmnopqrstuvwxyz" not in crit[0]["message"]
    assert "redacted" in crit[0]["message"]


def test_prreview_rejects_url_injection_in_repo_names():
    assert prreview.parse_pr_ref("https://github.com/a/../pull/3") == \
        (None, None, None)
    assert prreview._normalize_repo("own?x=1/repo") == ""
    assert prreview._normalize_repo("octo/hello.git") == "octo/hello"
    assert prreview.parse_pr_ref("octo/hello#4") == ("octo", "hello", "4")


def test_review_git_survives_non_utf8_output(monkeypatch):
    import subprocess

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        out = b"diff --git a/x b/x\n+++ b/x\n@@ -0,0 +1 @@\n+caf\xe9\n"
        if kw.get("text"):
            out = out.decode(kw.get("encoding") or "utf-8",
                             kw.get("errors") or "strict")
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    rep = review.review_git(".")
    assert isinstance(rep, str)
    assert seen.get("stdin") is subprocess.DEVNULL


# --------------------------------------------------------------------------
# ci
# --------------------------------------------------------------------------

def test_ci_skips_venv_and_word_matches_modules(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    (root / "tests" / "test_other.py").write_text("import data  # alpha, banana\n")
    (root / "tests" / "test_a.py").write_text("from pkg import a\n")
    (root / ".venv" / "lib" / "test_vendor.py").write_text("import a\n")
    log = EventLog(tmp_path / "ci.jsonl")
    pilot = ci.CIPilot(log, root)
    picked = pilot.impacted_tests(["pkg/a.py"])
    assert picked == ["tests/test_a.py"], picked
    assert not any(".venv" in p for p in pilot._scan())


def test_ci_records_are_bounded_and_restart_is_clean(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    log = EventLog(tmp_path / "ci.jsonl")
    pilot = ci.CIPilot(log, root, poll=0.01)
    pilot._snapshot = {}
    for i in range(ci._MAX_RECORDS + 20):
        (root / "f.txt").write_text(str(i))
        pilot._snapshot = {}
        assert pilot.check_once() is not None
    assert len(pilot.records) == ci._MAX_RECORDS
    pilot.start()
    first = pilot._thread
    pilot.stop()
    pilot.start()
    assert pilot._thread is not first and pilot._thread.is_alive()
    pilot.stop()
    assert not first.is_alive()


# --------------------------------------------------------------------------
# cov
# --------------------------------------------------------------------------

def test_cov_ignores_function_docstrings_and_global(tmp_path):
    src = ('X = 1\n'
           'def f():\n'
           '    """doc"""\n'
           '    global X\n'
           '    X += 1\n'
           '    return X\n')
    lines = cov.executable_lines(src)
    assert 3 not in lines and 4 not in lines, lines
    target = tmp_path / "m.py"
    target.write_text(src)
    import importlib.util
    spec = importlib.util.spec_from_file_location("covmod_t", target)
    mod = importlib.util.module_from_spec(spec)
    eng = cov.CoverageEngine(EventLog(tmp_path / "c.jsonl"))

    def subj():
        spec.loader.exec_module(mod)
        mod.f()
    res = eng.measure(str(target), subj)
    assert res.percent == 100.0, res
    assert sys.gettrace() is None


# --------------------------------------------------------------------------
# bandit
# --------------------------------------------------------------------------

def test_bandit_survives_non_positive_posteriors(tmp_path):
    log = EventLog(tmp_path / "b.jsonl")
    log.append("bandit.update", {"context": "chat", "arm": "m",
                                 "alpha": 0, "beta": -3})
    r = bandit.BanditRouter(log, ["m", "n"])
    rec = r.recommend("hello")          # used to ZeroDivisionError
    assert rec.arm in ("m", "n")


# --------------------------------------------------------------------------
# attest
# --------------------------------------------------------------------------

def test_attest_detects_tamper_of_last_record_and_hashless_envelope():
    ch = attest.AttestChain("s")
    ch.append("compile", "a")
    ch.append("wrap", "b", {"p": Path("/x")})       # non-JSON meta ok
    assert attest.verify_chain(ch).ok
    ch.records[-1].meta["evil"] = 1
    rep = attest.verify_chain(ch)
    assert not rep.ok and rep.broken_at == 1
    sealed = attest.seal_envelope("body text")
    stripped = " ".join(t for t in sealed.split(" ")
                        if not t.startswith("h="))
    assert attest.open_envelope(stripped)[0] is None
    q = "mentions [/attested-content] inside"
    body, r2 = attest.open_envelope(attest.seal_envelope(q))
    assert r2.ok and body == q


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def test_doctor_probes_run_concurrently(monkeypatch):
    fake = {f"p{i}": SimpleNamespace(base_url=f"http://h{i}")
            for i in range(4)}
    monkeypatch.setattr(doctor, "PROVIDERS", fake)

    def slow(url, timeout):
        time.sleep(0.3)
        return True, "HTTP 200", 300.0
    monkeypatch.setattr(doctor, "_probe", slow)
    t = time.monotonic()
    res = doctor._check_connectivity()
    assert time.monotonic() - t < 1.0
    assert [r["name"] for r in res] == [f"net:p{i}" for i in range(4)]


def test_doctor_reports_real_app_dir(monkeypatch, tmp_path):
    from fullagent import config
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    assert str(tmp_path) in doctor._check_app_dir()["detail"]


# --------------------------------------------------------------------------
# codeinit
# --------------------------------------------------------------------------

def test_codeinit_output_confined_to_project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("print(1)\n")
    out = codeinit._init_project_handler(str(proj), "../outside.md")
    assert out.startswith("error:") and not (tmp_path / "outside.md").exists()
    out = codeinit._init_project_handler(str(proj), str(tmp_path / "abs.md"))
    assert out.startswith("error:") and not (tmp_path / "abs.md").exists()
    ok = codeinit._init_project_handler(str(proj), "docs/AGENTS.md")
    assert ok.startswith("wrote ") and (proj / "docs" / "AGENTS.md").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_codeinit_does_not_block_on_fifo(tmp_path):
    os.mkfifo(tmp_path / "pipe.py")
    (tmp_path / "ok.py").write_text("a\nb\n")
    a = codeinit.analyze_project(tmp_path)     # used to block forever
    assert a["lines_total"] == 2


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def test_export_fence_and_truncated_key_redaction(tmp_path):
    key = "sk-" + "A" * (export.MD_RESULT_LIMIT + 50)
    msgs = [
        {"role": "assistant", "content": "x",
         "tool_calls": [{"id": "c", "function": {"name": "t",
                                                  "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c",
         "content": "a\n```\n# injected heading\n" + "y" * (
             export.MD_RESULT_LIMIT - 30) + key},
        "not-a-dict",
    ]
    exp = export.Exporter("s/../../evil", "m")
    md = exp.to_markdown(msgs)
    assert "AAAAAAAA" not in md            # truncated key still redacted
    assert "````" in md                    # fence longer than content's ```
    dp = exp.default_path("markdown")
    assert os.path.dirname(dp) == os.path.expanduser("~"), dp
    p = tmp_path / "out.md"
    exp.export_to_path(msgs, str(p))
    assert p.read_text().startswith("# Conversation export")
    if os.name == "posix":
        assert (p.stat().st_mode & 0o077) == 0
    assert not [x for x in os.listdir(tmp_path) if x.endswith(".tmp")]


# --------------------------------------------------------------------------
# backup
# --------------------------------------------------------------------------

def test_backup_with_absolute_symlink_is_still_restorable(tmp_path):
    app = tmp_path / "home"
    app.mkdir()
    (app / "config.json").write_text("{}")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-outside")
    os.symlink(str(outside), app / "abs-link")
    info = backup.create_backup(app)
    assert info["manifest"].get("skipped_unsafe_links") == ["abs-link"]
    (app / "config.json").write_text("changed")
    res = backup.restore_backup(info["path"], confirm=True, app_dir=app)
    assert res["ok"], res
    assert (app / "config.json").read_text() == "{}"


def test_restore_replaces_symlink_instead_of_writing_through(tmp_path):
    app = tmp_path / "home"
    app.mkdir()
    (app / "config.json").write_text("{\"v\": 1}")
    info = backup.create_backup(app)
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    (app / "config.json").unlink()
    os.symlink(str(victim), app / "config.json")
    res = backup.restore_backup(info["path"], confirm=True, app_dir=app)
    assert res["ok"], res
    assert victim.read_text() == "untouched"
    assert not (app / "config.json").is_symlink()
    assert (app / "config.json").read_text() == "{\"v\": 1}"


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def test_report_escapes_cells_and_survives_bad_numbers(tmp_path):
    log = EventLog(tmp_path / "r.jsonl")
    log.append("tool.call", {"name": "evil|name\nx"})
    log.append("cost.incurred", {"model": "m|1", "tokens_in": "12",
                                 "tokens_out": "x", "usd": "0.5"})
    assert report._safe_int(float("inf")) == 0       # used to Overflow
    assert report._safe_float("nan") == 0.0
    md = report.export_markdown(log)
    tool_rows = [ln for ln in md.splitlines() if "evil" in ln]
    assert tool_rows and all(ln.count(" | ") <= 4 for ln in tool_rows)
    assert "evil\\|name x" in md
    assert "m\\|1" in md


# --------------------------------------------------------------------------
# jsonout
# --------------------------------------------------------------------------

def test_jsonout_strict_json_nan_cycles_and_ascii_stdout():
    cyc = {"a": 1}
    cyc["self"] = cyc
    turn = SimpleNamespace(user_text="héllo \udcff", assistant_text="ok",
                           model_id="m", tools=[], usage=None, error="",
                           duration=float("nan"), cost_usd=float("inf"))
    obj = jsonout.turn_to_json(turn, "s", extra={"loop": cyc})
    assert obj["duration_s"] is None and obj["cost_usd"] is None
    assert obj["loop"]["self"] == "<circular>"
    raw = io.BytesIO()
    ascii_out = io.TextIOWrapper(raw, encoding="ascii")
    with redirect_stdout(ascii_out):
        jsonout.emit_json(obj)
    ascii_out.flush()
    line = raw.getvalue().decode("ascii")
    assert line.count("\n") == 1
    back = json.loads(line)
    assert back["user"].startswith("héllo")
    assert "NaN" not in line and "Infinity" not in line


# --------------------------------------------------------------------------
# streamlog
# --------------------------------------------------------------------------

@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setattr(streamlog, "LOG_DIR", str(tmp_path))
    return tmp_path


def test_streamlog_write_is_fast_and_rotation_bounded(logdir):
    t = time.monotonic()
    with streamlog.StreamLogger("w", max_bytes=4096) as lg:
        for i in range(5000):
            lg.write(f"line-{i:05d}")
    assert time.monotonic() - t < 3.0
    assert os.path.getsize(logdir / "w.log") < 4096
    assert os.path.getsize(logdir / "w.log.1") < 4096
    assert streamlog.tail_lines("w", 2) == ["line-04998", "line-04999"]


def test_streamlog_tail_caps_giant_line(logdir, monkeypatch):
    monkeypatch.setattr(streamlog, "TAIL_MAX_BYTES", 1024)
    (logdir / "g.log").write_bytes(b"x" * 100_000)
    got = streamlog.tail_lines("g", 5)
    assert len(got) == 1 and len(got[0]) <= 1024


def test_streamlog_follow_waits_for_complete_lines(logdir):
    p = logdir / "f.log"
    p.write_bytes(b"")
    streamlog.reset_follow_offset("f", 0)
    with open(p, "ab") as fh:
        fh.write(b"one\ntw")
    assert streamlog.follow("f", timeout=0.1) == ["one"]
    with open(p, "ab") as fh:
        fh.write(b"o\n")
    assert streamlog.follow("f", timeout=0.1) == ["two"]


def test_streamlog_follow_detects_rotation_past_old_offset(logdir):
    p = logdir / "r.log"
    p.write_bytes(b"old-1\nold-2\n")
    streamlog.reset_follow_offset("r", 0)
    assert streamlog.follow("r", timeout=0.1) == ["old-1", "old-2"]
    os.replace(p, str(p) + ".1")
    p.write_bytes(b"new-1\nnew-2\nnew-3\n")   # bigger than the old offset
    assert streamlog.follow("r", timeout=0.1) == ["new-1", "new-2", "new-3"]


# --------------------------------------------------------------------------
# cassette
# --------------------------------------------------------------------------

def test_cassette_corrupt_lines_and_dedup(tmp_path):
    path = tmp_path / "c.jsonl"
    msgs = [{"role": "user", "content": "hi"}]
    rec = cassette.Cassette(path, "record")
    rec.record("m", msgs, None, {"content": "ok"})
    rec.record("m", msgs, None, {"content": "ok"})
    rec.close()
    assert len(path.read_bytes().splitlines()) == 1
    with path.open("ab") as fh:
        fh.write(b"\xff\xfe\n42\n[1]\n")
    play = cassette.Cassette(path, "replay")      # used to raise
    assert play.replay("m", msgs, None) == {"content": "ok"}
    # a non-ASCII response round-trips through the byte-offset index
    rec2 = cassette.Cassette(path, "record")
    rec2.record("m", [{"role": "user", "content": "ü"}], None,
                {"content": "héllo"})
    rec2.close()
    play2 = cassette.Cassette(path, "replay")
    assert play2.replay("m", [{"role": "user", "content": "ü"}],
                        None) == {"content": "héllo"}


# --------------------------------------------------------------------------
# termsetup / voicein
# --------------------------------------------------------------------------

def test_termsetup_locale_precedence():
    assert termsetup.detect_unicode({"LC_ALL": "C",
                                     "LANG": "en_US.UTF-8"}) is False
    assert termsetup.detect_unicode({"LANG": "en_US.UTF-8"}) is True


def test_voicein_errors_not_printed_as_transcript(monkeypatch):
    monkeypatch.setattr(voicein, "whisper_backend", lambda: "whisper")
    monkeypatch.setattr(voicein, "record_and_transcribe",
                        lambda s: "recording timed out after 3s")
    ui = SimpleNamespace(infos=[], errors=[])
    ui.print_info = lambda t, c=None: ui.infos.append(t)
    ui.print_error = lambda t: ui.errors.append(t)
    voicein.handle_voice(ui, "3")
    assert ui.errors == ["recording timed out after 3s"]
    assert "-nostdin" in voicein._capture_cmd("ffmpeg", 1, "/tmp/x.wav")


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------

def test_merge_nested_key_order_and_causal_order(tmp_path):
    log = EventLog(tmp_path / "m.jsonl")
    log.append("user.message", {"text": "base"})
    anc = log.head()
    alt = log.fork(at_seq=anc, name="alt")
    log.checkout("main")
    log.append("user.message", {"text": "shared first"})
    log.append("tool.call", {"name": "write_file",
                             "args": {"path": "a.py", "content": "x"}})
    log.append("assistant.message", {"text": "main after"})
    log.checkout(alt)
    log.append("user.message", {"text": "shared first"})
    log.append("tool.call", {"name": "write_file",
                             "args": {"content": "x", "path": "a.py"}})
    branch_before = log.branch
    res = merge.TimelineMerger(log).merge("main", alt)
    # nested dict order no longer splits identical work in two
    assert len(res.shared) == 2 and not res.only_b, res.to_dict()
    texts = [e.data.get("text") for e in log.events(res.branch)
             if e.type in ("user.message", "assistant.message")]
    # shared work replays in causal position, not after everything else
    assert texts.index("shared first") < texts.index("main after"), texts
    assert branch_before == alt
    ok, msg = log.verify(res.branch)
    assert ok, msg


def test_merge_edit_conflict_uses_old_string(tmp_path):
    log = EventLog(tmp_path / "m2.jsonl")
    log.append("user.message", {"text": "base"})
    alt = log.fork(at_seq=log.head(), name="alt")
    log.checkout("main")
    log.append("tool.call", {"name": "edit_file", "args": {
        "path": "a.py", "old_string": "foo", "new_string": "z"}})
    log.checkout(alt)
    log.append("tool.call", {"name": "edit_file", "args": {
        "path": "a.py", "old_string": "bar", "new_string": "z"}})
    res = merge.TimelineMerger(log).merge("main", alt)
    assert [c["path"] for c in res.conflicts] == ["a.py"]
