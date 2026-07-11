"""Connection pooling — reuse HTTP/TCP connections for performance.

Provides a process-wide httpx.Client with connection pooling, keep-alive
and retry. All HTTP-based tools should use this instead of creating fresh
clients per request.

For providers (OpenAI, Anthropic, etc.) this gives a measurable speedup
on repeated calls by reusing TCP connections.
"""
from __future__ import annotations

import threading
from typing import Any

try:
    import httpx  # noqa: F401
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


_client: Any = None
_client_lock = threading.Lock()
_async_client: Any = None
_async_client_lock = threading.Lock()


def get_http_client(timeout: float = 30.0, max_connections: int = 100, max_keepalive: int = 20) -> Any:
    """Return a shared, pooled httpx.Client (creates one on first call)."""
    global _client
    if not _HAS_HTTPX:
        return None
    if _client is None:
        with _client_lock:
            if _client is None:
                limits = httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                )
                transport = httpx.HTTPTransport(retries=2, http2=True)
                _client = httpx.Client(
                    timeout=timeout,
                    limits=limits,
                    transport=transport,
                    follow_redirects=True,
                )
    return _client


def get_async_http_client(timeout: float = 30.0, max_connections: int = 100, max_keepalive: int = 20) -> Any:
    """Return a shared, pooled httpx.AsyncClient."""
    global _async_client
    if not _HAS_HTTPX:
        return None
    if _async_client is None:
        with _async_client_lock:
            if _async_client is None:
                limits = httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                )
                transport = httpx.AsyncHTTPTransport(retries=2, http2=True)
                _async_client = httpx.AsyncClient(
                    timeout=timeout,
                    limits=limits,
                    transport=transport,
                    follow_redirects=True,
                )
    return _async_client


def close_clients() -> None:
    """Close all pooled clients (call on shutdown)."""
    global _client, _async_client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
        _client = None
    if _async_client is not None:
        try:
            import asyncio
            asyncio.get_event_loop().run_until_complete(_async_client.aclose())
        except Exception:  # noqa: BLE001
            pass
        _async_client = None


def pooled_get(url: str, headers: dict[str, str] | None = None, timeout: float | None = None) -> Any:
    """Convenience GET using the pooled client."""
    client = get_http_client()
    if client is None:
        return None
    return client.get(url, headers=headers or {}, timeout=timeout) if timeout else client.get(url, headers=headers or {})


def pooled_post(url: str, json: dict | None = None, headers: dict[str, str] | None = None, timeout: float | None = None) -> Any:
    """Convenience POST using the pooled client."""
    client = get_http_client()
    if client is None:
        return None
    kwargs: dict[str, Any] = {"headers": headers or {}}
    if json is not None:
        kwargs["json"] = json
    if timeout is not None:
        kwargs["timeout"] = timeout
    return client.post(url, **kwargs)


def pool_stats() -> dict[str, Any]:
    """Return stats about the connection pool."""
    if not _HAS_HTTPX:
        return {"available": False}
    return {
        "available": True,
        "sync_client_active": _client is not None,
        "async_client_active": _async_client is not None,
        "library": "httpx",
    }
