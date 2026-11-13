"""Regression tests: webhook auth/limits, MCP reconnect, pid-reuse guards."""
import http.client
import json
import os
import queue
import sys
import textwrap
from types import SimpleNamespace

import pytest

from fullagent import mcp, supervise, webhook


@pytest.fixture
def hookenv(tmp_path, monkeypatch):
    monkeypatch.setenv("FULLAGENT_WEBHOOK_REGISTRY", str(tmp_path / "wh.json"))
    monkeypatch.setenv("FULLAGENT_WEBHOOK_TOKEN_FILE", str(tmp_path / "tok"))
    monkeypatch.delenv("FULLAGENT_WEBHOOK_ENABLE", raising=False)
    monkeypatch.delenv("FULLAGENT_WEBHOOK_DISABLE", raising=False)
    webhook.shutdown_server()
    yield tmp_path
    webhook.shutdown_server()


def _post(port, path, body=b"{}", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_webhook_not_started_without_opt_in(hookenv):
    agent = SimpleNamespace(tools={}, webhook_queue=None)
    webhook.register(agent)
    assert webhook.webhook_server_port() is None
    assert "WebhookAdd" in agent.tools
    assert isinstance(agent.webhook_queue, queue.Queue)
    assert agent.webhook_queue.maxsize == webhook.MAX_QUEUED_PROMPTS


def test_webhook_requires_token_and_rejects_browsers(hookenv):
    marker = hookenv / "ran"
    agent = SimpleNamespace(tools={}, webhook_queue=None)
    webhook.register(agent, port=0)
    port = webhook.webhook_server_port()
    assert port
    webhook._handle_webhook_add("x", f"touch {marker}")
    assert (hookenv / "tok").stat().st_mode & 0o777 == 0o600

    status, _ = _post(port, "/hook/x")
    assert status == 401 and not marker.exists()
    status, _ = _post(port, "/hook/x",
                      headers={webhook.TOKEN_HEADER: "wrong-token-123456"})
    assert status == 401 and not marker.exists()
    good = {webhook.TOKEN_HEADER: webhook.webhook_token()}
    status, _ = _post(port, "/hook/x",
                      headers=dict(good, Origin="https://evil.example"))
    assert status == 403 and not marker.exists()
    status, _ = _post(port, "/hook/x",
                      headers=dict(good, Host="evil.example:80"))
    assert status == 403 and not marker.exists()

    status, data = _post(port, "/hook/x", headers=good)
    assert status == 200 and json.loads(data)["ok"] is True
    assert marker.exists()
    # Bearer form works too
    marker.unlink()
    status, _ = _post(port, "/hook/x", headers={
        "Authorization": "Bearer " + webhook.webhook_token()})
    assert status == 200 and marker.exists()


def test_webhook_rejects_oversized_body(hookenv):
    agent = SimpleNamespace(tools={}, webhook_queue=None)
    webhook.register(agent, port=0)
    port = webhook.webhook_server_port()
    webhook._handle_webhook_add("big", "cat > /dev/null")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest("POST", "/hook/big")
    conn.putheader(webhook.TOKEN_HEADER, webhook.webhook_token())
    conn.putheader("Content-Length", str(webhook.MAX_BODY_BYTES + 1))
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 413
    conn.close()


def test_webhook_agent_queue_is_bounded(hookenv):
    q = queue.Queue(maxsize=1)
    hook = {"id": "1", "path": "p", "action": "agent:hello"}
    assert webhook.dispatch(hook, b"", q)["queued"] is True
    out = webhook.dispatch(hook, b"", q)  # full: refused, never blocks
    assert out["queued"] is False


# -- MCP ----------------------------------------------------------------------

_FAKE_SERVER = textwrap.dedent("""
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        m = msg["method"]
        if m == "initialize":
            res = {"protocolVersion": "2024-11-05", "capabilities": {}}
        elif m == "tools/list":
            res = {"tools": [{"name": "die"}, {"name": "ping"}]}
        elif msg["params"]["name"] == "die":
            sys.exit(0)
        else:
            res = {"content": [{"type": "text", "text": "pong"}]}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}),
              flush=True)
""")


def test_mcp_reconnects_after_server_death(tmp_path):
    script = tmp_path / "srv.py"
    script.write_text(_FAKE_SERVER)
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"servers": {"s": {
        "command": sys.executable, "args": [str(script)]}}, "timeout": 5}))
    mgr = mcp.MCPManager(str(cfg))
    try:
        tools = {t["name"]: mcp._make_tool(mgr, "s", t)
                 for t in mgr.discover("s")}
        assert tools["ping"].handler() == "pong"
        first = mgr._clients["s"]._proc
        assert tools["die"].handler().startswith("ERROR")
        first.wait(5)
        # used to fail forever: alive stayed True on a dead process
        assert tools["ping"].handler() == "pong"
        assert mgr._clients["s"]._proc is not first
        assert first.returncode is not None  # reaped, no zombie
    finally:
        mgr.shutdown()


# -- pid reuse guards ---------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_supervise_owned_group_refuses_foreign_or_own_group():
    assert supervise._owned_group(os.getpid(), "") is False  # not a leader
    assert supervise._owned_group(1, "") is False
    assert supervise._owned_group("junk", "") is False
    assert supervise._owned_group(2 ** 22 + 12345, "") is False
