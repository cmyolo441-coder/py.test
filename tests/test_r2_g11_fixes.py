import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import speculate, supervise  # noqa: E402








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
