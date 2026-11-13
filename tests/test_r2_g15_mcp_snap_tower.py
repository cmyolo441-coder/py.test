import os
import stat
import sys
import tempfile

os.environ.setdefault("FULLAGENT_HOME", tempfile.mkdtemp())

from pathlib import Path

from fullagent import mcp
from fullagent.snapshots import SnapshotStore

_SERVER = r'''
import json, sys
for raw in sys.stdin:
    msg = json.loads(raw)
    rid = msg.get("id")
    m = msg.get("method")
    if m == "initialize":
        r = {"jsonrpc": "2.0", "id": rid, "result": {}}
    elif m == "tools/call":
        r = {"jsonrpc": "2.0", "id": rid,
             "error": {"code": -1, "message": "request failed: 404"}}
    else:
        continue
    sys.stdout.write(json.dumps(r) + "\n"); sys.stdout.flush()
'''


def test_mcp_server_error_keeps_connection_and_reports_message(tmp_path):
    srv = tmp_path / "srv.py"
    srv.write_text(_SERVER)
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"servers": {"s": {"command": "%s", "args": ["%s"]}}}'
                   % (sys.executable, srv))
    mgr = mcp.MCPManager(config_path=str(cfg))
    try:
        tool = mcp._make_tool(mgr, "s", {"name": "t"})
        out = tool.handler()
        assert "request failed: 404" in out, out
        client = mgr._clients["s"]
        pid = client._proc.pid
        assert mgr.is_connected("s")
        tool.handler()
        # same process reused, not killed and respawned per call
        assert mgr._clients["s"]._proc.pid == pid
    finally:
        mgr.shutdown()


class _UI:
    def __init__(self):
        self.agent = None
        self.infos, self.errors = [], []

    def print_info(self, t, color=None):
        self.infos.append(t)

    def print_error(self, t):
        self.errors.append(t)


def test_handle_mcp_lists_non_string_args(tmp_path, monkeypatch):
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"servers": {"s": {"command": "nope-xyz", '
                   '"args": ["--port", 8080]}}}')
    monkeypatch.setattr(mcp, "CONFIG_PATH", str(cfg))
    ui = _UI()
    mcp.handle_mcp(ui, "")
    assert ui.infos and "--port 8080" in ui.infos[0]


def test_snapshot_restore_keeps_executable_bit(tmp_path):
    store = SnapshotStore(tmp_path / "store")
    f = tmp_path / "run.sh"
    f.write_text("echo one\n")
    f.chmod(0o755)
    snap = store.take([str(f)])
    f.write_text("echo two\n")
    store.materialise(snap["tree"])
    assert f.read_text() == "echo one\n"
    assert stat.S_IMODE(f.stat().st_mode) == 0o755


def test_tower_null_text_is_empty_command():
    from types import SimpleNamespace
    from fullagent.tower import Tower
    t = Tower(SimpleNamespace(log=None))
    assert t.command({"text": None}) == {"ok": False, "error": "empty command"}
