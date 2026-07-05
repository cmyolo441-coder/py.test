"""Middleware pipeline for processing requests before/after model calls.

Middleware are simple callables that wrap a handler, enabling cross-cutting
concerns (logging, metrics, redaction) to be composed cleanly.
"""
from __future__ import annotations

import re
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], dict[str, Any]]
Middleware = Callable[[dict[str, Any], Handler], dict[str, Any]]


class Pipeline:
    """Composes middleware around a terminal handler."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self._middleware: list[Middleware] = []

    def use(self, middleware: Middleware) -> "Pipeline":
        self._middleware.append(middleware)
        return self

    def run(self, context: dict[str, Any]) -> dict[str, Any]:
        handler = self._handler
        for mw in reversed(self._middleware):
            current = handler

            def wrapped(ctx: dict[str, Any], mw=mw, nxt=current) -> dict[str, Any]:
                return mw(ctx, nxt)

            handler = wrapped
        return handler(context)


_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|tak_[A-Za-z0-9_-]{8,})")


def redaction_middleware(context: dict[str, Any], nxt: Handler) -> dict[str, Any]:
    """Redact secrets from the input text before it is processed."""
    text = context.get("input", "")
    context["input"] = _SECRET_RE.sub("[REDACTED]", text)
    return nxt(context)


def logging_middleware(context: dict[str, Any], nxt: Handler) -> dict[str, Any]:
    from .logging_config import get_logger

    log = get_logger("pipeline")
    log.info("request received")
    result = nxt(context)
    log.info("request completed")
    return result
