"""Retry helpers with exponential backoff and jitter."""

from __future__ import annotations

import functools
import random
import time
from typing import Callable, Type, TypeVar

T = TypeVar("T")


def retry(
    exceptions: tuple[Type[BaseException], ...] = (Exception,),
    tries: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    jitter: float = 0.1,
) -> Callable:
    """Decorator that retries a function on the given exceptions."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            _delay = delay
            last_exc: BaseException | None = None
            for attempt in range(1, tries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt == tries:
                        break
                    sleep_for = _delay + random.uniform(0, jitter)
                    time.sleep(sleep_for)
                    _delay *= backoff
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
