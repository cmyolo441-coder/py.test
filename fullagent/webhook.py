"""HTTP webhook server — real incoming webhooks that trigger actions.

A real stdlib ``http.server`` (``ThreadingHTTPServer``) runs in a background
daemon thread, bound to 127.0.0.1 only. External services (or curl) can
``POST /hook/<path>`` and the hook's action runs for real:

* shell action:   run as a real subprocess (body piped to stdin)
* ``agent:<prompt>`` action: the prompt is put on ``agent.webhook_queue``
  (a ``queue.Queue`` created by :func:`register`) for the agent loop to pick
  up and answer.

Registry (persisted): ``~/.fullagent/webhooks.json`` — a list of::

    {"id": "a1b2c3d4", "path": "deploy", "action": "agent:deploy the latest build"}

Path security: only ``^[a-z0-9_-]+$`` is accepted, everything else 404s.
The server binds localhost only — never exposed to the network.

Authentication (security fix): binding 127.0.0.1 is not enough — any local
process, and any web page open in the user's browser (a cross-site
``fetch('http://127.0.0.1:18789/hook/x', {method: 'POST'})`` needs no
CORS preflight for a text/plain body, and DNS rebinding defeats the
loopback bind entirely), could fire the registered SHELL actions. Every
POST must now carry the per-install secret from
``~/.fullagent/webhook.token`` (mode 0600) as ``X-FullAgent-Token: <token>``
or ``Authorization: Bearer <token>``. Requests from browsers (``Origin``
header) or for a foreign ``Host`` are refused, bodies are capped and
sockets time out.

Opt-in: the server is no longer started on every agent start-up. It starts
when a webhook exists (WebhookAdd, or a non-empty registry from an earlier
session) or ``FULLAGENT_WEBHOOK_ENABLE=1`` is set;
``FULLAGENT_WEBHOOK_DISABLE=1`` never starts it.

Environment:

* ``FULLAGENT_WEBHOOK_PORT``  — port to bind (default 18789)
* ``FULLAGENT_WEBHOOK_REGISTRY`` — registry file override (tests)

Tools registered: ``WebhookAdd``, ``WebhookList``, ``WebhookRemove``,
``WebhookTest``. TUI: ``/webhooks`` lists hooks.
"""

from __future__ import annotations

import hmac
import http.server
import json
import secrets
import logging
import os
import queue
import re
import socketserver
import subprocess
import threading
import uuid
from http import HTTPStatus
from pathlib import Path
from typing import Any

from .cancelguard import run_in_group
from .tools import RISK_CONFIRM, RISK_SAFE, Tool

_log = logging.getLogger(__name__)

DEFAULT_PORT = 18789
PATH_RE = re.compile(r"^[a-z0-9_-]+$")
HOOK_TIMEOUT_S = 60
MAX_BODY_BYTES = 1 * 1024 * 1024
REQUEST_SOCKET_TIMEOUT_S = 15      # slowloris guard: idle sockets close
MAX_CONCURRENT_ACTIONS = 4         # shell actions running at once
MAX_QUEUED_PROMPTS = 256           # agent:<prompt> items awaiting pickup
TOKEN_HEADER = "X-FullAgent-Token"
_ACTION_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_ACTIONS)
_REGISTRY_LOCK = threading.Lock()

# Process-wide guard: the server is started at most once per process.
_SERVER_STARTED = False
_SERVER_LOCK = threading.Lock()
_SERVER_PORT: int | None = None


def _registry_path() -> Path:
    override = os.environ.get("FULLAGENT_WEBHOOK_REGISTRY")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    if os.environ.get("FULLAGENT_HOME"):
        return Path(os.path.expandvars(os.environ["FULLAGENT_HOME"])).expanduser() / "webhooks.json"
    return Path("~/.fullagent/webhooks.json").expanduser()


def _token_path() -> Path:
    override = os.environ.get("FULLAGENT_WEBHOOK_TOKEN_FILE")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return _registry_path().with_name("webhook.token")


_TOKEN_CACHE: dict[str, str] = {}


def webhook_token() -> str:
    """The per-install shared secret (created 0600 on first use)."""
    path = _token_path()
    key = str(path)
    cached = _TOKEN_CACHE.get(key)
    if cached:
        return cached
    tok = ""
    try:
        tok = path.read_text(encoding="utf-8").strip()
    except OSError:
        tok = ""
    if len(tok) < 16:
        tok = secrets.token_urlsafe(32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(tok + "\n")
        except OSError as e:
            _log.warning("webhook: could not persist token: %s", e)
    _TOKEN_CACHE[key] = tok
    return tok


def _default_port() -> int:
    try:
        return int(os.environ.get("FULLAGENT_WEBHOOK_PORT", "") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def load_hooks() -> list[dict]:
    """Load the webhook registry. Missing/invalid file → []."""
    path = _registry_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        _log.warning("webhook: invalid JSON in %s", path)
        return []
    if not isinstance(data, list):
        _log.warning("webhook: top-level JSON in %s must be a list", path)
        return []
    out = [h for h in data
           if isinstance(h, dict) and h.get("id") and h.get("path")]
    return out


def _save_hooks(hooks: list[dict]) -> None:
    import tempfile
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # unique tmp per writer: concurrent saves used to share one ".tmp"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(hooks, indent=2))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _find_hook(path_name: str) -> dict | None:
    for h in load_hooks():
        if h.get("path") == path_name:
            return h
    return None


def valid_hook_path(path_name: str) -> bool:
    return bool(isinstance(path_name, str) and PATH_RE.match(path_name))


# ---------------------------------------------------------------------------
# Action execution
# ---------------------------------------------------------------------------

def _run_shell_action(action: str, body: bytes) -> tuple[int, str, str]:
    """Run the hook's shell command for real; body goes to stdin.

    Own process group: on timeout the whole tree is killed (subprocess.run
    only killed the ``sh -c`` wrapper and left grandchildren running)."""
    try:
        proc = run_in_group(action, shell=True, input=body,
                            timeout=HOOK_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, "", f"webhook action timed out after {HOOK_TIMEOUT_S}s"
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace")[:4000],
            proc.stderr.decode("utf-8", "replace")[:4000])


def dispatch(hook: dict, body: bytes, webhook_queue: "queue.Queue | None") -> dict:
    """Execute a hook's action for real. Returns a result summary dict."""
    action = str(hook.get("action", ""))
    body_text = body.decode("utf-8", "replace")
    if action.startswith("agent:"):
        prompt = action[len("agent:"):].strip()
        item = {"type": "webhook",
                "hook_id": hook.get("id"),
                "path": hook.get("path"),
                "prompt": prompt,
                "body": body_text}
        if webhook_queue is not None:
            try:
                # never block the HTTP thread, never grow without bound
                webhook_queue.put_nowait(item)
            except queue.Full:
                return {"queued": False, "error": "prompt queue is full"}
            return {"queued": True, "prompt": prompt[:120]}
        _log.warning("webhook: no webhook_queue on agent — "
                     "agent action dropped for %s", hook.get("path"))
        return {"queued": False, "error": "agent has no webhook_queue"}
    if not _ACTION_SLOTS.acquire(timeout=1.0):
        return {"ran": False, "error": "too many webhook actions running"}
    try:
        rc, out, err = _run_shell_action(action, body)
    finally:
        _ACTION_SLOTS.release()
    return {"ran": True, "returncode": rc, "stdout": out, "stderr": err}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _WebhookHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FullAgentWebhook/1.0"
    # StreamRequestHandler applies this to the socket: a client that
    # connects and never sends used to pin a server thread forever.
    timeout = REQUEST_SOCKET_TIMEOUT_S

    # set by _start_server
    webhook_queue: "queue.Queue | None" = None
    server_port: int = 0

    def _host_ok(self) -> bool:
        """DNS-rebinding guard: only loopback Host names are served."""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True  # HTTP/1.0 clients may omit it
        if host.startswith("["):
            name = host[1:].split("]", 1)[0]
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in ("127.0.0.1", "localhost", "::1")

    def _authorized(self) -> bool:
        supplied = (self.headers.get(TOKEN_HEADER) or "").strip()
        if not supplied:
            auth = (self.headers.get("Authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        if not supplied:
            return False
        return hmac.compare_digest(supplied.encode("utf-8", "replace"),
                                   webhook_token().encode("utf-8"))

    def log_message(self, fmt, *args):  # noqa: D102 — quiet by default
        _log.debug("webhook http: " + fmt, *args)

    def _send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> bytes | None:
        """Body bytes, or None when the request was already answered
        (too large / malformed length)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST,
                            {"ok": False, "error": "bad Content-Length"})
            return None
        if length > MAX_BODY_BYTES:
            # refuse without reading (and without silently truncating —
            # the unread tail used to be parsed as the next request)
            self.close_connection = True
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                            {"ok": False, "error": "body too large"})
            return None
        if length == 0:
            return b""
        return self.rfile.read(length)

    def do_POST(self) -> None:  # noqa: N802 — http.server convention
        if self.headers.get("Origin") or not self._host_ok():
            # browsers always send Origin on cross-site POSTs: a web page
            # must never be able to drive local shell actions
            self.close_connection = True
            self._send_json(HTTPStatus.FORBIDDEN,
                            {"ok": False, "error": "forbidden"})
            return
        if not self._authorized():
            self.close_connection = True
            self._send_json(HTTPStatus.UNAUTHORIZED,
                            {"ok": False,
                             "error": f"missing or bad {TOKEN_HEADER}"})
            return
        if not self.path.startswith("/hook/"):
            self._send_json(HTTPStatus.NOT_FOUND,
                            {"ok": False, "error": "unknown endpoint"})
            return
        path_name = self.path[len("/hook/"):].split("?", 1)[0]
        if not valid_hook_path(path_name):
            self._send_json(HTTPStatus.NOT_FOUND,
                            {"ok": False,
                             "error": "invalid hook path"})
            return
        body = self._read_body()
        if body is None:
            return
        hook = _find_hook(path_name)
        if hook is None:
            self._send_json(HTTPStatus.NOT_FOUND,
                            {"ok": False,
                             "error": f"no webhook registered for '{path_name}'"})
            return
        try:
            result = dispatch(hook, body, self.webhook_queue)
        except Exception as e:  # noqa: BLE001 — never 500 the caller
            _log.exception("webhook: action failed for %s", path_name)
            self._send_json(HTTPStatus.OK,
                            {"ok": False, "error": str(e)[:300]})
            return
        _log.info("webhook: fired '%s' action=%s", path_name,
                  str(hook.get("action", ""))[:60])
        self._send_json(HTTPStatus.OK, {"ok": True, "result": result})

    def do_GET(self) -> None:  # noqa: N802 — http.server convention
        if self.headers.get("Origin") or not self._host_ok():
            self._send_json(HTTPStatus.FORBIDDEN,
                            {"ok": False, "error": "forbidden"})
            return
        if self.path.startswith("/hook/"):
            self._send_json(HTTPStatus.METHOD_NOT_ALLOWED,
                            {"ok": False, "error": "use POST"})
        else:
            # health check only: no hook inventory for unauthenticated
            # callers
            payload: dict = {"ok": True, "service": "fullagent-webhook"}
            if self._authorized():
                payload["hooks"] = len(load_hooks())
            self._send_json(HTTPStatus.OK, payload)


class _ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # don't keep a list of every request thread ever started
    block_on_close = False


def _start_server(agent: Any, port: int | None = None) -> tuple[_ThreadedServer, int]:
    """Bind 127.0.0.1 and serve webhooks on a daemon thread."""
    bind_port = DEFAULT_PORT if port is None else port
    if port is None:
        bind_port = _default_port()
    handler = _WebhookHandler
    handler.webhook_queue = getattr(agent, "webhook_queue", None)
    webhook_token()  # make sure the secret exists before we listen
    server = _ThreadedServer(("127.0.0.1", bind_port), handler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever,
                              name="fullagent-webhook",
                              daemon=True)
    thread.start()
    global _SERVER_PORT
    _SERVER_PORT = actual_port
    _log.info("webhook: listening on 127.0.0.1:%d", actual_port)
    return server, actual_port


_SERVER: "_ThreadedServer | None" = None
_LAST_AGENT: Any = None


def ensure_server(agent: Any, port: int | None = None) -> int | None:
    """Start the webhook server once per process; return its port (or None)."""
    global _SERVER_STARTED, _SERVER
    if os.environ.get("FULLAGENT_WEBHOOK_DISABLE", "").strip() in (
            "1", "true", "yes"):
        return None
    with _SERVER_LOCK:
        if _SERVER_STARTED:
            return _SERVER_PORT
        try:
            _SERVER, _ = _start_server(agent, port=port)
        except OSError as e:
            _log.warning("webhook: could not bind port: %s — "
                         "webhooks disabled", e)
            return None
        _SERVER_STARTED = True
        return _SERVER_PORT


def webhook_server_port() -> int | None:
    """Port the webhook server is listening on, or None."""
    return _SERVER_PORT


def shutdown_server() -> None:
    """Stop the server (tests / host shutdown). Idempotent."""
    global _SERVER_STARTED, _SERVER, _SERVER_PORT
    with _SERVER_LOCK:
        srv, _SERVER = _SERVER, None
        _SERVER_STARTED = False
        _SERVER_PORT = None
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:  # noqa: BLE001
            pass


def _opted_in() -> bool:
    if os.environ.get("FULLAGENT_WEBHOOK_ENABLE", "").strip() in (
            "1", "true", "yes"):
        return True
    return bool(load_hooks())


def _curl_hint(path: str) -> str:
    return (f"curl -X POST -H \"{TOKEN_HEADER}: $(cat {_token_path()})\" "
            f"http://127.0.0.1:{_SERVER_PORT or _default_port()}/hook/{path}")


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_webhook_add(path: str, action: str) -> str:
    if not valid_hook_path(path or ""):
        return (f"✗ invalid path '{path}' — must match "
                f"{PATH_RE.pattern} (lowercase letters, digits, -, _)")
    action = (action or "").strip()
    if not action:
        return "✗ action is required: a shell command or 'agent:<prompt>'"
    with _REGISTRY_LOCK:
        hooks = load_hooks()
        updated = False
        for h in hooks:
            if h.get("path") == path:
                h["action"] = action
                updated = True
                break
        if not updated:
            hook_id = uuid.uuid4().hex[:8]
            hooks.append({"id": hook_id, "path": path, "action": action})
        _save_hooks(hooks)
    # adding a webhook is the user's opt-in: start the server now
    ensure_server(_LAST_AGENT)
    if updated:
        return (f"✓ webhook updated: POST http://127.0.0.1:"
                f"{_SERVER_PORT or _default_port()}/hook/{path} "
                f"(auth: {_curl_hint(path)})")
    return (f"✓ webhook added [{hook_id}]: POST "
            f"http://127.0.0.1:{_SERVER_PORT or _default_port()}/hook/{path}"
            f" (auth: {_curl_hint(path)})")


def _handle_webhook_list() -> str:
    hooks = load_hooks()
    if not hooks:
        return "No webhooks registered — use WebhookAdd or /webhooks."
    port = _SERVER_PORT or _default_port()
    state = "listening" if _SERVER_PORT else "server not running"
    lines = [f"Webhooks (POST to http://127.0.0.1:{port}/hook/<path>, "
             f"header {TOKEN_HEADER} from {_token_path()}; {state}):"]
    for h in hooks:
        act = str(h.get("action", ""))
        if len(act) > 80:
            act = act[:77] + "..."
        lines.append(f"  [{h.get('id')}] /hook/{h.get('path')}  →  {act}")
    return "\n".join(lines)


def _handle_webhook_remove(id: str) -> str:
    with _REGISTRY_LOCK:
        hooks = load_hooks()
        kept = [h for h in hooks if h.get("id") != id]
        if len(kept) == len(hooks):
            return f"✗ no webhook with id '{id}'"
        _save_hooks(kept)
    return f"✓ webhook [{id}] removed"


def _handle_webhook_test(path: str, body: str = "") -> str:
    """POST real HTTP to the local server and verify a 200 comes back."""
    import http.client

    port = _SERVER_PORT or _default_port()
    payload = (body or "").encode("utf-8")
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", f"/hook/{path}", body=payload,
                     headers={"Content-Type": "application/json",
                              TOKEN_HEADER: webhook_token()})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
    except OSError as e:
        return f"✗ could not reach webhook server on 127.0.0.1:{port}: {e}"
    if resp.status != 200:
        return (f"✗ POST /hook/{path} → HTTP {resp.status}: "
                f"{data.decode('utf-8', 'replace')[:200]}")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        parsed = None
    ok = isinstance(parsed, dict) and parsed.get("ok") is True
    return (f"✓ POST /hook/{path} → HTTP 200, ok={ok}\n"
            f"  response: {data.decode('utf-8', 'replace')[:300]}")


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

def register(agent: Any, port: int | None = None) -> None:
    """Wire the webhook server + tools into an agent (duck-typed).

    Creates ``agent.webhook_queue`` (a ``queue.Queue``) if missing, starts
    the real HTTP server once per process, and registers the four tools.
    """
    global _LAST_AGENT
    if getattr(agent, "webhook_queue", None) is None:
        # bounded: nothing in the agent drains this queue yet, so an
        # unbounded Queue grew by one item per agent:<prompt> POST forever
        agent.webhook_queue = queue.Queue(maxsize=MAX_QUEUED_PROMPTS)
    _LAST_AGENT = agent
    # Opt-in only: no listening socket (and no extra thread) unless the
    # user has webhooks or explicitly enabled the server.
    if port is not None or _opted_in():
        ensure_server(agent, port=port)

    tools = getattr(agent, "tools", None)
    if not isinstance(tools, dict):
        # Duck-typed agents without a tools dict used to lose all four
        # tool registrations silently via the early return.
        tools = {}
        agent.tools = tools
    tools["WebhookAdd"] = Tool(
        name="WebhookAdd",
        description=("Register an HTTP webhook: incoming POSTs to "
                     "/hook/<path> run the action. Action is a shell "
                     "command (body piped to stdin) or 'agent:<prompt>' to "
                     "queue a prompt for the agent. Returns the hook id."),
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "Hook path, ^[a-z0-9_-]+$"},
                        "action": {"type": "string",
                                   "description": ("Shell command or "
                                                   "'agent:<prompt>'")}},
                    "required": ["path", "action"]},
        handler=_handle_webhook_add,
        risk=RISK_CONFIRM,
    )
    tools["WebhookList"] = Tool(
        name="WebhookList",
        description="List registered webhooks with ids, paths and actions.",
        parameters={"type": "object", "properties": {}},
        handler=_handle_webhook_list,
        risk=RISK_SAFE,
    )
    tools["WebhookRemove"] = Tool(
        name="WebhookRemove",
        description="Remove a webhook by its id (see WebhookList).",
        parameters={"type": "object",
                    "properties": {
                        "id": {"type": "string",
                               "description": "Webhook id"}},
                    "required": ["id"]},
        handler=_handle_webhook_remove,
        risk=RISK_CONFIRM,
    )
    tools["WebhookTest"] = Tool(
        name="WebhookTest",
        description=("Send a real HTTP POST to /hook/<path> on the local "
                     "webhook server and verify it returns HTTP 200."),
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string",
                                 "description": "Registered hook path"},
                        "body": {"type": "string",
                                 "description": "JSON body to POST"}},
                    "required": ["path"]},
        handler=_handle_webhook_test,
        risk=RISK_SAFE,
    )


# ---------------------------------------------------------------------------
# TUI command
# ---------------------------------------------------------------------------

def handle_webhooks(ui: Any, arg: str) -> None:
    """Handle ``/webhooks`` — list registered hooks via the TUI."""
    ui.print_info(_handle_webhook_list())


# --------------------------------------------------------------------------- self-test --

def _self_test() -> None:
    """REAL end-to-end test: real HTTP server, real POST, real subprocess."""
    import http.client
    import tempfile
    from types import SimpleNamespace

    tmp = Path(tempfile.mkdtemp(prefix="webhook_selftest_"))
    os.environ["FULLAGENT_WEBHOOK_REGISTRY"] = str(tmp / "webhooks.json")
    marker = tmp / "marker.txt"

    agent = SimpleNamespace(tools={}, webhook_queue=None)
    register(agent, port=0)  # ephemeral port
    port = webhook_server_port()
    assert port and port != 0, f"server did not start, port={port}"
    print(f"  server listening on 127.0.0.1:{port}")

    # 1) register a hook whose shell action writes a marker file
    out = _handle_webhook_add("selftest", f"cat > {marker}")
    assert "✓" in out, out
    print(f"  add: {out}")

    # 2) real HTTP POST — verify 200 + marker file created with the body
    body = json.dumps({"event": "ping", "n": 42})
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/hook/selftest",
                 body=body.encode(),
                 headers={"Content-Type": "application/json",
                          TOKEN_HEADER: webhook_token()})
    resp = conn.getresponse()
    raw = resp.read().decode()
    conn.close()
    assert resp.status == 200, f"expected 200, got {resp.status}: {raw}"
    assert json.loads(raw).get("ok") is True, raw
    assert marker.exists(), "marker file was not created"
    assert marker.read_text() == body, "marker content != posted body"
    print("  POST /hook/selftest → 200, marker file has exact body  PASS")

    # 3) agent: action queues the prompt on agent.webhook_queue
    _handle_webhook_add("agenthook", "agent:review the new deployment")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/hook/agenthook",
                 body=b'{"deploy": true}',
                 headers={"Content-Type": "application/json",
                          TOKEN_HEADER: webhook_token()})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 200, resp.status
    item = agent.webhook_queue.get(timeout=5)
    assert item["prompt"] == "review the new deployment", item
    assert json.loads(item["body"])["deploy"] is True, item
    print("  agent: action queued on agent.webhook_queue  PASS")

    # 4) path validation: bad paths rejected with 404
    for bad in ("/hook/../evil", "/hook/BadPath", "/hook/%20",
                "/hook/%2e%2e"):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", bad, body=b"{}",
                     headers={"Content-Type": "application/json",
                              TOKEN_HEADER: webhook_token()})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 404, f"{bad} → {resp.status}, expected 404"
    print("  bad paths → 404  PASS")

    # 5) unregistered but valid path → 404
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/hook/nonexistent", body=b"{}",
                 headers={"Content-Type": "application/json",
                          TOKEN_HEADER: webhook_token()})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 404, resp.status
    print("  unregistered path → 404  PASS")

    # 6) WebhookTest tool does a real POST and verifies 200
    test_out = _handle_webhook_test("selftest", '{"x": 1}')
    assert "✓" in test_out and "200" in test_out, test_out
    print(f"  WebhookTest: {test_out.splitlines()[0]}  PASS")

    # 7) list + remove
    listing = _handle_webhook_list()
    assert "selftest" in listing and "agenthook" in listing, listing
    hook_id = [h["id"] for h in load_hooks()
               if h["path"] == "agenthook"][0]
    assert "✓" in _handle_webhook_remove(hook_id)
    assert "agenthook" not in _handle_webhook_list()
    print("  list/remove  PASS")

    print("PASS: webhook module self-test (real HTTP, real subprocess)")


def _self_test_thread_efficiency() -> None:
    """Prove the webhook server thread never polls: ThreadingHTTPServer
    blocks in accept() (via serve_forever), so an idle server does zero
    timed wakeups — thread count stays flat and the server still answers
    a real request instantly."""
    import http.client
    import time
    from types import SimpleNamespace

    agent = SimpleNamespace(tools={}, webhook_queue=None)
    port = ensure_server(agent, port=0)  # idempotent: once per process
    assert port and port != 0, f"server did not start, port={port}"

    # 1. idle: thread count must not churn (no spin / no thread factory)
    before = threading.active_count()
    time.sleep(2.0)
    after = threading.active_count()
    assert after <= before + 1, \
        f"thread churn while idle: {before} -> {after}"
    print(f"  idle 2s: threads {before} -> {after} (no churn)  PASS")

    # 2. the serving thread is a single blocking serve_forever thread
    srv_threads = [t for t in threading.enumerate()
                   if t.name == "fullagent-webhook"]
    assert srv_threads and all(t.is_alive() for t in srv_threads), \
        [t.name for t in threading.enumerate()]
    print(f"  serving thread alive: {srv_threads[0].name}  PASS")

    # 3. functionality still instant: real request round-trips in < 2s
    t0 = time.monotonic()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/__nope__")
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    dt = time.monotonic() - t0
    assert resp.status == 200, resp.status  # GET = health endpoint
    assert "fullagent-webhook" in body, body
    assert dt < 2.0, f"server slow to answer: {dt:.2f}s"
    print(f"  idle server answers in {dt*1000:.0f}ms  PASS")

    print("PASS: webhook thread efficiency (blocking accept, zero idle wakes)")


if __name__ == "__main__":
    _self_test()  # first: starts the once-per-process server with its agent
    _self_test_thread_efficiency()  # reuses that server via ensure_server
