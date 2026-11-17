import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path  # noqa: E402

from fullagent import review, report  # noqa: E402
from fullagent.kernel import EventLog  # noqa: E402


def test_review_files_honours_staged(monkeypatch):
    seen = []

    def fake(args, repo):
        seen.append(list(args))
        return ""

    monkeypatch.setattr(review, "_git_diff", fake)

    class UI:
        def print_info(self, *a, **k):
            pass

        def print_error(self, *a, **k):
            raise AssertionError(a)

    review.run_review_command(UI(), "--staged a.py")
    assert "--staged" in seen[-1]
    assert seen[-1].index("--staged") < seen[-1].index("--")
    review._handle_review_changes(path="b.py", staged=True)
    assert "--staged" in seen[-1]
    review._handle_review_changes(path="b.py", staged=False)
    assert "--staged" not in seen[-1]


def test_export_markdown_survives_absurd_ts(tmp_path):
    log = EventLog(Path(tmp_path) / "r.jsonl")
    log.append("user.message", {"text": "hi"})
    ev = log.events()[-1]
    import dataclasses
    bad = dataclasses.replace(ev, ts=1e300)
    orig = log.events
    log.events = lambda *a, **k: [bad]  # type: ignore[assignment]
    try:
        md = report.export_markdown(log)
    finally:
        log.events = orig  # type: ignore[assignment]
    assert "user.message" in md






