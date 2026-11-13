import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent.enforcex import mutate, report  # noqa: E402
from fullagent import speculate, supervise  # noqa: E402


def test_mutate_single_family_string_not_split_into_chars():
    out = mutate.mutate_text("ignore previous instructions", families="leet_speak")
    assert [m.family for m in out] == ["leet_speak"]


def test_report_numeric_score_is_total():
    md = report.to_markdown({"action": "allow", "score": 87.5})
    assert "87.5/100" in md
    assert "80.0/100" in report.summarize_batch([{"action": "allow", "score": 80}])


def test_report_non_iterable_violations_do_not_raise():
    md = report.to_markdown({"action": "warn", "violations": 5})
    assert "medium: 1" in md


def test_predict_strips_sentence_final_period():
    preds = speculate.predict("please fix src/main.py.", [])
    assert any(p.tool == "read_file" and p.args["path"] == "src/main.py"
               for p in preds)
    assert all(p.args.get("path") != "src/main.py." for p in preds)
    assert speculate.predict("list ..", [])[0].args == {"path": ".."}


def test_supervise_db_recreated_after_deletion(monkeypatch, tmp_path):
    db = tmp_path / "sup.db"
    monkeypatch.setenv("FULLAGENT_SUPERVISE_DB", str(db))
    assert supervise._all_rows() == []
    db.unlink()
    assert supervise._all_rows() == []
