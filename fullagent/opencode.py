"""OpenCode Console bridge — free models (hy4-flash-free) inside FullAgent.

Self-contained: free OpenCode models are only reachable from inside the
OpenCode harness. This module therefore bootstraps its own private OpenCode
CLI service when needed (installs the CLI on first use, starts `opencode
serve --service` in the background, reads its generated Basic-auth password)
and then talks to that local service. No account, API key or manual setup is
required — everything lives in this file.

An already-running OpenCode desktop service is reused when present.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from . import config
from .client import APIError, StreamResult

DEFAULT_PORT = 28894
DESKTOP_SERVICE = Path.home() / ".config" / "opencode" / "service.json"


# ---------------------------------------------------------------------------
# Runtime discovery / bootstrap
# ---------------------------------------------------------------------------

def _runtime_dir() -> Path:
    override = os.environ.get("FULLAGENT_OPENCODE_HOME", "").strip()
    p = Path(override) if override else (Path.home() / ".fullagent" / "opencode-runtime")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _runtime_env(runtime: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(runtime)
    env["USERPROFILE"] = str(runtime)
    env["XDG_CONFIG_HOME"] = str(runtime / ".config")
    env["XDG_DATA_HOME"] = str(runtime / ".local" / "share")
    env["XDG_STATE_HOME"] = str(runtime / ".local" / "state")
    return env


def _service_credentials(path: Path) -> tuple[int, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    password = str(data.get("password", "")).strip()
    if not password:
        return None
    port = data.get("port", DEFAULT_PORT)
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return port, password


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return True


def _probe(port: int, password: str, timeout: float = 2.0) -> bool:
    try:
        r = requests.get(
            f"http://127.0.0.1:{port}/api/model",
            headers={"Authorization": "Basic " + base64.b64encode(
                f"opencode:{password}".encode()).decode()},
            timeout=timeout,
        )
        if r.status_code == 200:
            return any(m.get("id") == "hy4-flash-free"
                       for m in r.json().get("data", []))
    except Exception:
        pass
    return False


def _find_binary(runtime: Path, env: dict[str, str]) -> str | None:
    explicit = os.environ.get("OPENCODE_BIN", "").strip()
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("opencode")
    if found:
        return found
    for candidate in (
        runtime / ".opencode" / "bin" / "opencode",
        runtime / ".opencode" / "bin" / "opencode.exe",
        Path.home() / ".opencode" / "bin" / "opencode",
        Path.home() / ".opencode" / "bin" / "opencode.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _install_binary(runtime: Path, env: dict[str, str]) -> str:
    """Install the official OpenCode CLI into the private runtime dir."""
    script = (
        'set -e; curl -fsSL https://opencode.ai/v2/install '
        '| bash -s -- --no-modify-path'
    )
    try:
        subprocess.run(["bash", "-lc", script], env=env, timeout=600,
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                       check=True)
    except (OSError, subprocess.SubprocessError):
        # npm fallback (works anywhere Node is available).
        npm_dir = runtime / "npm"
        subprocess.run(
            ["npm", "install", "--prefix", str(npm_dir), "@opencode/cli"],
            env=env, timeout=600, stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT, check=True)
    binary = _find_binary(runtime, env)
    if not binary:
        raise APIError("could not install the OpenCode CLI (need curl+bash or npm)")
    return binary


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _start_service(binary: str, runtime: Path, env: dict[str, str]) -> tuple[int, str]:
    """Start a private OpenCode service and wait until it is usable."""
    port = _free_port()
    subprocess.run([binary, "service", "set", "port", str(port)], env=env,
                   timeout=60, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)

    log = runtime / "service.log"
    kwargs: dict[str, Any] = dict(
        env=env, stdin=subprocess.DEVNULL,
        stdout=open(log, "ab", buffering=0),
        stderr=subprocess.STDOUT,
    )
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | 0x00000008
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([binary, "serve", "--service"], **kwargs)

    state_path = runtime / "bridge.json"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        creds = _service_credentials(runtime / ".config" / "opencode" / "service.json")
        if creds and _probe(port, creds[1]):
            state_path.write_text(json.dumps({
                "url": f"http://127.0.0.1:{port}",
                "password": creds[1],
                "pid": proc.pid,
            }), encoding="utf-8")
            return port, creds[1]
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    raise APIError(f"OpenCode service failed to start. Log: {log}")


_server_cache: tuple[str, str] | None = None


def ensure_server(provider=None) -> tuple[str, str]:
    """Return (base_url, password), reusing or bootstrapping the service."""
    global _server_cache
    if _server_cache:
        base, pw = _server_cache
        try:
            port = int(base.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            port = DEFAULT_PORT
        if _probe(port, pw, timeout=1.5):
            return base, pw
        _server_cache = None

    env_password = os.environ.get("OPENCODE_BRIDGE_PASSWORD", "").strip()
    env_url = os.environ.get("OPENCODE_BRIDGE_URL", "").strip()
    if env_url and env_password:
        _server_cache = (env_url, env_password)
        return _server_cache

    # 1) Reuse an already-running desktop service.
    desktop = _service_credentials(DESKTOP_SERVICE)
    if desktop and _probe(desktop[0], desktop[1]):
        _server_cache = (f"http://127.0.0.1:{desktop[0]}", desktop[1])
        return _server_cache

    # 2) Reuse our own previously started service.
    runtime = _runtime_dir()
    state_path = runtime / "bridge.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        url = str(state["url"])
        pw = str(state["password"])
        pid = int(state.get("pid", 0))
        port = int(url.rsplit(":", 1)[1])
        if _alive(pid) and _probe(port, pw):
            _server_cache = (url, pw)
            return _server_cache
    except (OSError, ValueError, KeyError, TypeError):
        pass

    # 3) Bootstrap a private CLI service (installs the CLI if necessary).
    env = _runtime_env(runtime)
    binary = _find_binary(runtime, env) or _install_binary(runtime, env)
    port, pw = _start_service(binary, runtime, env)
    _server_cache = (f"http://127.0.0.1:{port}", pw)
    return _server_cache


def is_opencode(provider) -> bool:
    return getattr(provider, "key", "") == "opencode"


def _headers(password: str) -> dict[str, str]:
    token = base64.b64encode(f"opencode:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(p.get("text", "")) if isinstance(p, dict) else str(p)
            for p in content)
    return ""


def _flatten(messages: list[dict]) -> str:
    return "\n\n".join(
        f"{str(m.get('role', 'user')).capitalize()}: "
        f"{_content_text(m.get('content', ''))}"
        for m in messages)


def _raise(r: requests.Response, what: str) -> None:
    if r.status_code < 200 or r.status_code >= 300:
        raise APIError(f"OpenCode {what} failed ({r.status_code}): {r.text[:500]}",
                       status=r.status_code)


def _run(provider, model, effort, messages: list[dict], tools,
         timeout: float, on_token, on_reasoning, on_tool_start,
         on_tool_args, should_cancel, on_status) -> StreamResult:
    base, password = ensure_server(provider)
    session = requests.Session()
    session.headers.update(_headers(password))
    sid = ""
    try:
        r = session.post(base + "/api/session", json={}, timeout=timeout)
        _raise(r, "create session")
        try:
            sid = r.json()["data"]["id"]
        except (KeyError, TypeError, ValueError) as exc:
            raise APIError(f"OpenCode create session: {r.text[:500]}") from exc

        r = session.post(
            base + f"/api/session/{sid}/model",
            json={"model": {"id": model.id, "providerID": "opencode"}},
            timeout=timeout)
        _raise(r, "switch model")

        r = session.post(base + f"/api/session/{sid}/prompt",
                         json={"text": _flatten(messages)}, timeout=timeout)
        _raise(r, "send prompt")

        deadline = time.monotonic() + max(timeout, 60)
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise APIError("cancelled")
            r = session.get(base + f"/api/session/{sid}/message", timeout=timeout)
            _raise(r, "read messages")
            for msg in r.json().get("data", []):
                kind = msg.get("type", "")
                if kind == "error":
                    raise APIError(f"OpenCode: {msg.get('error') or msg.get('message') or msg}")
                if kind != "assistant":
                    continue
                parts = msg.get("content", [])
                reasoning = "".join(str(p.get("text", "")) for p in parts
                                    if isinstance(p, dict) and p.get("type") == "reasoning")
                content = "".join(str(p.get("text", "")) for p in parts
                                  if isinstance(p, dict) and p.get("type") == "text")
                if not content:
                    continue
                if on_reasoning and reasoning:
                    on_reasoning(reasoning)
                if on_token:
                    for i in range(0, len(content), 24):
                        if should_cancel and should_cancel():
                            break
                        on_token(content[i:i + 24])
                tokens = msg.get("tokens") or {}
                return StreamResult(
                    content=content, reasoning=reasoning, finish_reason="stop",
                    usage={"prompt_tokens": tokens.get("input", 0),
                           "completion_tokens": tokens.get("output", 0),
                           "total_tokens": tokens.get("input", 0) + tokens.get("output", 0)},
                    model=model.id)
            time.sleep(0.25)
        raise APIError("OpenCode response timed out")
    finally:
        if sid:
            try:
                session.delete(base + f"/api/session/{sid}", timeout=5)
            except Exception:
                pass


def chat_stream(provider, model, effort, messages, tools,
                on_token=None, on_reasoning=None, on_tool_start=None,
                on_tool_args=None, should_cancel=None,
                timeout: float = config.DEFAULT_TIMEOUT,
                on_status=None, **_ignored) -> StreamResult:
    return _run(provider, model, effort, messages, tools, timeout,
                on_token, on_reasoning, on_tool_start, on_tool_args,
                should_cancel, on_status)


def chat_blocking(provider, model, effort, messages, tools,
                  timeout: float = config.DEFAULT_TIMEOUT,
                  should_cancel=None, **_ignored) -> StreamResult:
    return _run(provider, model, effort, messages, tools, timeout,
                None, None, None, None, should_cancel, None)
