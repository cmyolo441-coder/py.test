"""Retry helpers with exponential backoff.

Wraps flaky network calls (API requests) so transient failures are retried
automatically. Uses tenacity when available; otherwise a small built-in
backoff implementation is used.
"""
from __future__ import annotations

import time
from functools import wraps
from typing import Callable, TypeVar

T = TypeVar("T")

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    _HAS_TENACITY = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_TENACITY = False


RETRYABLE_MESSAGES = (
    "timeout",
    "temporarily",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "connection",
)


def _is_retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(token in message for token in RETRYABLE_MESSAGES)


def with_retry(
    max_attempts: int = 3, base_delay: float = 1.0, max_delay: float = 10.0
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function on transient errors."""

    if _HAS_TENACITY:

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            return retry(
                reraise=True,
                stop=stop_after_attempt(max_attempts),
                wait=wait_exponential(multiplier=base_delay, max=max_delay),
                retry=retry_if_exception_type(Exception),
            )(func)

        return decorator

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == max_attempts or not _is_retryable(exc):
                        raise
                    time.sleep(min(delay, max_delay))
                    delay *= 2
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
