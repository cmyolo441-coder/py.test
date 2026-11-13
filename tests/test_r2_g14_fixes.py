import json
import os
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from fullagent.enforcex import warroom  # noqa: E402
from fullagent.kernel import EventLog  # noqa: E402
from fullagent.world import WorldModel, Edge  # noqa: E402
from fullagent.workflows import WorkflowStep, WorkflowError  # noqa: E402
import fullagent.config as config  # noqa: E402


def test_warroom_load_tolerates_non_dict_event_data(tmp_path):
    p = tmp_path / "wr.jsonl"
    p.write_text(json.dumps({"type": "incident", "data": {
        "incident_id": "A", "timeline": [{"kind": "note", "data": "x"}]}})
        + "\n" + json.dumps({"type": "incident", "data": {
            "incident_id": "B", "detections": 5}}) + "\n")
    store = warroom.IncidentStore(str(p))
    assert len(store) == 2


def test_warroom_ingest_non_json_values_never_raises(tmp_path):
    store = warroom.IncidentStore(str(tmp_path / "wr.jsonl"))
    ids = store.ingest([{"rule_id": "R", "evidence": {1, 2}}])
    assert ids and len(store) == 1
    store.shutdown()


def test_world_impact_excludes_touched_file_on_cycle(tmp_path):
    w = WorldModel(EventLog(tmp_path / "w.jsonl"))
    w.edges = {("a.py", "b.py"): Edge("a.py", "b.py"),
               ("b.py", "a.py"): Edge("b.py", "a.py")}
    paths = [r["path"] for r in w.predict_impact("a.py").ranked]
    assert paths == ["b.py"]


def test_workflow_step_infinite_phase_is_workflow_error():
    try:
        WorkflowStep.from_dict({"task": "t", "phase": float("inf")})
    except WorkflowError:
        return
    raise AssertionError("expected WorkflowError")


def test_config_load_infinite_int_does_not_crash(tmp_path, monkeypatch):
    cf = tmp_path / "config.json"
    cf.write_text('{"prune_window": 1e999, "theme": "x"}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", cf)
    cfg = config.Config.load()
    assert cfg.prune_window == 40 and cfg.theme == "x"


def test_diff_panel_title_escapes_markup_in_path(tmp_path, monkeypatch):
    from rich.console import Console
    from fullagent.diffpreview import render_rich_diff_panel
    monkeypatch.chdir(tmp_path)
    c = Console(width=100, record=True)
    c.print(render_rich_diff_panel(
        "write_file", {"path": "x[/]y.txt", "content": "a\n"}))
    c.print(render_rich_diff_panel(
        "write_file", {"path": "app/[id]/page.tsx", "content": "a\n"}))
    out = c.export_text()
    assert "x[/]y.txt" in out and "app/[id]/page.tsx · " in out
