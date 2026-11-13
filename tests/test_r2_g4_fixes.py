import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path  # noqa: E402

from fullagent import tools  # noqa: E402
from fullagent.fuzzymatch import find_best_match  # noqa: E402


def test_apply_patch_added_blank_line(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("a\nb\nc\n")
    patch = f"--- a/{fp}\n+++ b/{fp}\n@@ -1,3 +1,4 @@\n a\n+\n b\n c\n"
    out = tools.apply_patch(patch)
    assert out.startswith("Updated"), out
    assert fp.read_text() == "a\n\nb\nc\n"


def test_apply_patch_removed_blank_line(tmp_path):
    fp = tmp_path / "f.py"
    fp.write_text("a\n\nb\n")
    patch = f"--- a/{fp}\n+++ b/{fp}\n@@ -1,3 +1,2 @@\n a\n-\n b\n"
    out = tools.apply_patch(patch)
    assert out.startswith("Updated"), out
    assert fp.read_text() == "a\nb\n"


def test_nan_timeout_rejected():
    assert tools.run_command("true", timeout="nan").startswith("ERROR")


def test_streaming_read_total_when_offset_past_eof(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_READ_WHOLE_MAX_BYTES", 10)
    fp = tmp_path / "big.txt"
    fp.write_text("".join(f"line{i}\n" for i in range(50)))
    out = tools.read_file(str(fp), offset=1000)
    assert "50 lines total" in out, out
    out = tools.read_file(str(fp), offset=45, limit=3)
    assert "50 lines total" in out and "showing 45..47" in out, out


def test_fuzzy_leading_indent_does_not_swallow_previous_newline():
    c = "def f():\n    x = 1\n    return x\n"
    s, e, _ = find_best_match(c, "    x  =  1")
    assert c[s:e] == "    x = 1"


def test_debate_empty_run_clears_stale_clusters(tmp_path):
    from fullagent.debate import DebateTournament
    from fullagent.kernel import EventLog

    def speaker(model_id, prompt):
        return "rust is right" if model_id in ("A", "B") else "go wins big"

    t = DebateTournament(EventLog(Path(tmp_path) / "d.jsonl"), speaker,
                         ["A", "B", "C"])
    t.run("rust or go?")
    t.run("")  # degenerate run: no outcome
    trust = t.refute("A")
    assert trust["A"] > 0.5 and trust["B"] < 0.5
