import os
import tempfile
from pathlib import Path

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent.kernel import EventLog  # noqa: E402
from fullagent.tuner import ParzenTuner  # noqa: E402


def test_nan_score_never_wins(tmp_path: Path):
    t = ParzenTuner(EventLog(tmp_path / "t.jsonl"), {"a": [1, 2, 3]})
    t.observe({"a": 1}, float("nan"))
    t.observe({"a": 2}, 0.5)
    t.observe({"a": 3}, 0.9)
    assert t.best().config == {"a": 3}
    t.suggest()  # ranking with NaN must not raise
