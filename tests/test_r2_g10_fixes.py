import os
import sqlite3
import tarfile
import io
import json
import tempfile
from pathlib import Path

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent import backup, costtrack, dbtools, router, voicein  # noqa: E402
from fullagent.enforcex import critic  # noqa: E402
from fullagent.kernel import EventLog  # noqa: E402


def test_backup_restores_hardlinked_files(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "a.txt").write_text("hello")
    os.link(app / "a.txt", app / "b.txt")
    info = backup.create_backup(app)
    (app / "a.txt").unlink()
    (app / "b.txt").unlink()
    res = backup.restore_backup(info["path"], app_dir=app)
    assert res["ok"], res
    assert (app / "a.txt").read_text() == "hello"
    assert (app / "b.txt").read_text() == "hello"


def test_backup_non_dict_manifest_is_refused(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    p = tmp_path / "m.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        body = b"[]"
        ti = tarfile.TarInfo(backup.MANIFEST_NAME)
        ti.size = len(body)
        t.addfile(ti, io.BytesIO(body))
    res = backup.restore_backup(str(p), app_dir=app)
    assert not res["ok"] and "corrupt" in res["message"]


def test_dbquery_select_without_space_is_read_only(tmp_path):
    p = tmp_path / "a.db"
    c = sqlite3.connect(p)
    c.execute("create table t(x)")
    c.execute("insert into t values (7)")
    c.commit()
    c.close()
    assert "7" in dbtools.db_query(str(p), "SELECT*FROM t")
    assert not dbtools.db_query(str(p), "VALUES(1)").startswith("ERROR")
    assert "confirm" in dbtools.db_query(str(p), "delete from t")


def test_costtrack_infinite_token_count_in_file(tmp_path, monkeypatch):
    monkeypatch.setattr(costtrack, "COST_DIR", str(tmp_path))
    (tmp_path / "s1.json").write_text(
        '{"m": {"turns": 1, "in": Infinity, "out": 2}, '
        '"ok": {"turns": 2, "in": 3, "out": 4}}')
    t = costtrack.CostTracker("s1")
    assert t.totals()["turns"] == 2


def test_costtrack_bad_tokens_do_not_half_count(tmp_path, monkeypatch):
    monkeypatch.setattr(costtrack, "COST_DIR", str(tmp_path))
    t = costtrack.CostTracker("s2")
    t.add_turn("m", 10, "garbage")
    assert t.totals()["turns"] == 0


def test_router_choose_none_task(tmp_path):
    r = router.Router(EventLog(Path(tmp_path) / "l.jsonl"))
    choice = r.choose(None)
    assert choice.model_id


def test_voice_record_infinite_seconds():
    assert voicein.record_and_transcribe(float("inf")) == \
        "seconds must be an integer"


def test_critic_mk_rule_fallback(monkeypatch):
    monkeypatch.setattr(critic, "_rx", None)
    r = critic._mk_rule("R1", "Never say x", keywords=("abc",),
                        severity="high")
    assert r.id == "R1" and r.text == "Never say x"
    assert r.keywords == ("abc",) and r.severity == "high"
